"""LLM inference engine — Claude Code CLI backend.

This is the SOLE LLM inference path for the framework. Every Claude-powered
component (evaluator, hallucination detector, case generator, report generator,
meta-agent) calls :class:`LLMBackend`, which shells out to the `claude` CLI in
non-interactive print mode:

    claude -p "<prompt>" --output-format json --model <model> --system-prompt <sys>

The CLI uses the machine's existing logged-in Claude session, so no API key is
required. Structured-output helpers force JSON by instructing the model and
robustly extracting the first JSON object from the reply.

Design goals:
  * Resilience: exponential backoff + jitter on transient failures, graceful
    degradation (never raise into scoring code — return a typed error result).
  * Observability: every call is logged with latency and token usage.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from config import CONFIG, FrameworkConfig


# ── logging ──────────────────────────────────────────────────────────────────
import logging

logger = logging.getLogger("atf.llm")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(name)s] %(levelname)s %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


@dataclass
class LLMResult:
    """Outcome of a single LLM call."""

    text: str
    ok: bool
    latency_ms: float
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return not self.ok


class LLMBackendError(RuntimeError):
    """Raised only by callers that explicitly opt into hard failures."""


class LLMBackend:
    """Thin, resilient wrapper around the `claude` CLI."""

    def __init__(self, config: Optional[FrameworkConfig] = None) -> None:
        self.config = config or CONFIG
        self.call_log: List[Dict[str, Any]] = []
        self._available: Optional[bool] = None

    # ── availability ─────────────────────────────────────────────────────
    def is_available(self) -> bool:
        """True if the claude binary is resolvable on PATH."""
        if self._available is None:
            self._available = shutil.which(self.config.claude_binary) is not None
        return self._available

    # ── preflight ────────────────────────────────────────────────────────
    def ping(self, model: str) -> "tuple[bool, Optional[str]]":
        """One tiny call to check a model id is accepted. (ok, error)."""
        res = self.complete("Reply with OK.", model=model, timeout=60)
        return res.ok, res.error

    def verify_models(self, models: List[str]) -> Dict[str, str]:
        """Ping each unique model; return {model: error} for those that fail."""
        bad: Dict[str, str] = {}
        for m in dict.fromkeys(m for m in models if m):
            ok, err = self.ping(m)
            if not ok:
                bad[m] = err or "unknown error"
        return bad

    # ── core call ────────────────────────────────────────────────────────
    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> LLMResult:
        """Run one prompt through the CLI, with retries and backoff.

        Never raises for transient/inference errors — returns an ``LLMResult``
        with ``ok=False`` so scoring code can degrade gracefully.
        """
        model = model or self.config.judge_model
        timeout = timeout or self.config.llm_timeout_seconds

        if not self.is_available():
            return LLMResult(
                text="",
                ok=False,
                latency_ms=0.0,
                model=model,
                error=f"claude binary '{self.config.claude_binary}' not found on PATH",
            )

        cmd = [
            self.config.claude_binary,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--model",
            model,
        ]
        if system:
            cmd += ["--system-prompt", system]

        last_error = "unknown error"
        attempts = max(1, self.config.max_retries)
        for attempt in range(1, attempts + 1):
            start = time.perf_counter()
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                latency_ms = (time.perf_counter() - start) * 1000.0
                result = self._parse_cli_output(proc, model, latency_ms)
                if result.ok:
                    self._record(model, result, attempt, ok=True)
                    return result
                last_error = result.error or "cli reported error"
            except subprocess.TimeoutExpired:
                latency_ms = (time.perf_counter() - start) * 1000.0
                last_error = f"timeout after {timeout}s"
            except Exception as exc:  # pragma: no cover - defensive
                latency_ms = (time.perf_counter() - start) * 1000.0
                last_error = f"{type(exc).__name__}: {exc}"

            logger.warning(
                "LLM call failed (attempt %d/%d, model=%s): %s",
                attempt,
                attempts,
                model,
                last_error,
            )
            if attempt < attempts:
                self._sleep_backoff(attempt)

        failed = LLMResult(
            text="",
            ok=False,
            latency_ms=0.0,
            model=model,
            error=last_error,
        )
        self._record(model, failed, attempts, ok=False)
        return failed

    def complete_json(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        default: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Complete and parse the reply as JSON.

        Appends an explicit JSON-only instruction to the prompt and extracts the
        first JSON object from the response. On any failure returns ``default``
        (or ``{}``) with an ``_llm_error`` key so callers can detect degradation.
        """
        json_prompt = (
            prompt
            + "\n\nRespond with ONLY a single valid JSON object. "
            "No markdown, no code fences, no commentary before or after the JSON."
        )
        res = self.complete(json_prompt, system=system, model=model, timeout=timeout)
        base = dict(default or {})
        base["_llm_latency_ms"] = res.latency_ms
        base["_llm_model"] = res.model
        if res.failed:
            base["_llm_error"] = res.error
            return base
        parsed = extract_json(res.text)
        if parsed is None:
            base["_llm_error"] = "could not parse JSON from response"
            base["_llm_raw_text"] = res.text[:500]
            return base
        parsed["_llm_latency_ms"] = res.latency_ms
        parsed["_llm_model"] = res.model
        return parsed

    # ── helpers ──────────────────────────────────────────────────────────
    def _parse_cli_output(
        self, proc: "subprocess.CompletedProcess[str]", model: str, latency_ms: float
    ) -> LLMResult:
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()

        if not stdout:
            return LLMResult(
                text="",
                ok=False,
                latency_ms=latency_ms,
                model=model,
                error=f"empty stdout (rc={proc.returncode}) stderr={stderr[:200]}",
            )
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Some CLI versions print plain text; treat non-JSON stdout as text.
            if proc.returncode == 0:
                return LLMResult(
                    text=stdout, ok=True, latency_ms=latency_ms, model=model
                )
            return LLMResult(
                text="",
                ok=False,
                latency_ms=latency_ms,
                model=model,
                error=f"non-JSON output (rc={proc.returncode})",
            )

        is_error = bool(data.get("is_error", False))
        text = data.get("result", "") or ""
        usage = data.get("usage", {}) or {}
        inp = int(usage.get("input_tokens", 0) or 0)
        out = int(usage.get("output_tokens", 0) or 0)
        if is_error:
            return LLMResult(
                text="",
                ok=False,
                latency_ms=latency_ms,
                model=model,
                error=str(text) or "cli is_error=true",
                raw=data,
            )
        return LLMResult(
            text=str(text),
            ok=True,
            latency_ms=latency_ms,
            model=model,
            input_tokens=inp,
            output_tokens=out,
            total_tokens=inp + out,
            raw=data,
        )

    def _sleep_backoff(self, attempt: int) -> None:
        delay = min(
            self.config.retry_base_delay * (2 ** (attempt - 1)),
            self.config.retry_max_delay,
        )
        delay += random.uniform(0, delay * 0.25)  # jitter
        time.sleep(delay)

    def _record(self, model: str, result: LLMResult, attempts: int, ok: bool) -> None:
        entry = {
            "model": model,
            "ok": ok,
            "attempts": attempts,
            "latency_ms": round(result.latency_ms, 1),
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "error": result.error,
        }
        self.call_log.append(entry)
        logger.info(
            "LLM call model=%s ok=%s latency=%.0fms tokens=%d/%d",
            model,
            ok,
            result.latency_ms,
            result.input_tokens,
            result.output_tokens,
        )

    @property
    def total_latency_ms(self) -> float:
        return sum(c["latency_ms"] for c in self.call_log)

    @property
    def total_tokens(self) -> int:
        return sum(c["input_tokens"] + c["output_tokens"] for c in self.call_log)


# ── module-level JSON extraction ──────────────────────────────────────────────
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort extraction of the first JSON object from a model reply.

    Handles: raw JSON, fenced ```json blocks, and JSON embedded in prose.
    Returns ``None`` if nothing parseable is found.
    """
    if not text:
        return None
    text = text.strip()

    # 1) direct parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 2) fenced code block
    m = _FENCE_RE.search(text)
    if m:
        try:
            obj = json.loads(m.group(1).strip())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # 3) first balanced {...} span
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        try:
                            obj = json.loads(candidate)
                            if isinstance(obj, dict):
                                return obj
                        except json.JSONDecodeError:
                            break
        start = text.find("{", start + 1)
    return None


# Shared default backend instance.
DEFAULT_BACKEND = LLMBackend()
