"""LLM-as-Judge — Claude-powered evaluation of agent responses.

``ClaudeJudge`` is the framework's evaluation brain. It uses the Claude Code CLI
(via :class:`core.llm_client.LLMBackend`) to score agent responses against
playbook rules and to power the qualitative metrics (reasoning quality, tone,
semantic task completion, remediation adequacy). It also generates the natural
language report narrative (executive summary + recommendations).

All calls degrade gracefully: if the LLM is unavailable or returns garbage, a
neutral, clearly-flagged result is returned rather than crashing the run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.llm_client import DEFAULT_BACKEND, LLMBackend
from core.playbook_loader import Playbook, Rule
from core.trace_capture import AgentTrace

JUDGE_SYSTEM = (
    "You are an expert AI agent evaluator. Your role is to assess AI agent "
    "responses against defined playbook rules with precise, unbiased scoring. "
    "You must return structured JSON evaluation results. Be calibrated: reserve "
    "scores above 90 for genuinely excellent compliance and below 40 for clear "
    "violations. Judge only what the evidence supports. Keep every 'reasoning' "
    "field to at most two sentences — be terse."
)


@dataclass
class JudgeResult:
    """Result of judging one rule (or one qualitative dimension)."""

    score: float                       # 0..100
    passed: bool
    confidence: float                  # 0..1
    reasoning: str
    violations: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    degraded: bool = False             # True if the LLM call failed / was faked
    latency_ms: float = 0.0
    model: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "pass": self.passed,
            "confidence": round(self.confidence, 3),
            "reasoning": self.reasoning,
            "violations": self.violations,
            "recommendations": self.recommendations,
            "degraded": self.degraded,
            "latency_ms": round(self.latency_ms, 1),
            "model": self.model,
        }


def _truncate(text: str, limit: int = 6000) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "\n…[truncated]…"


def _trace_digest(trace: Optional[AgentTrace], limit: int = 4000) -> str:
    if trace is None:
        return "(no trace available)"
    lines: List[str] = []
    for s in trace.steps:
        prefix = s.step_type.upper()
        if s.step_type in ("tool_call", "tool_result") and s.tool_name:
            params = s.tool_params if s.tool_params else ""
            lines.append(f"[{prefix}] {s.tool_name} {params} -> {s.tool_result}")
        else:
            lines.append(f"[{prefix}] {s.content}")
    return _truncate("\n".join(lines) or "(no steps)", limit)


class ClaudeJudge:
    """Claude-powered evaluator. One instance is shared across a run."""

    def __init__(self, backend: Optional[LLMBackend] = None, model: Optional[str] = None) -> None:
        self.backend = backend or DEFAULT_BACKEND
        self.model = model  # None => backend default (judge_model)

    # ── rule adherence (the core LLM-as-Judge) ────────────────────────────
    def evaluate_rule(
        self, rule: Rule, response: str, trace: Optional[AgentTrace] = None
    ) -> JudgeResult:
        """Score a response's adherence to a single playbook rule (0-100)."""
        rule_block = (
            f"id: {rule.id}\n"
            f"name: {rule.name}\n"
            f"description: {rule.description}\n"
            f"priority: {rule.priority.value}\n"
            f"expected_behavior: {rule.expected_behavior}\n"
            f"failure_examples: {rule.failure_examples}"
        )
        user = (
            f"PLAYBOOK RULE:\n{rule_block}\n\n"
            f"AGENT RESPONSE:\n{_truncate(response)}\n\n"
            f"AGENT TRACE:\n{_trace_digest(trace)}\n\n"
            "Score this response on a scale of 0-100 for adherence to the rule. "
            "Return JSON with exactly these keys:\n"
            "{\n"
            '  "score": int,            // 0-100 adherence\n'
            '  "pass": bool,            // true if the rule is satisfied\n'
            '  "confidence": float,     // 0.0-1.0 your confidence\n'
            '  "reasoning": string,     // concise justification\n'
            '  "violations": [string],  // specific violations, [] if none\n'
            '  "recommendations": [string] // how to improve, [] if none\n'
            "}"
        )
        data = self.backend.complete_json(user, system=JUDGE_SYSTEM, model=self.model)
        return self._to_result(data, pass_floor=60.0)

    def evaluate_rules_batch(
        self, rules: List[Rule], response: str, trace: Optional[AgentTrace] = None
    ) -> Dict[str, JudgeResult]:
        """Score ALL playbook rules in a single LLM call (major latency win).

        Returns a mapping of rule_id -> JudgeResult. Any rule the model omits (or
        the whole call, if it fails) degrades to a neutral, flagged result.
        """
        if not rules:
            return {}
        rules_block = "\n".join(
            f"- {r.id} | priority={r.priority.value} | {r.name}: {r.description} "
            f"(expected: {r.expected_behavior})"
            for r in rules
        )
        user = (
            "Evaluate an AI agent's response against EACH playbook rule below.\n\n"
            f"PLAYBOOK RULES:\n{rules_block}\n\n"
            f"AGENT RESPONSE:\n{_truncate(response)}\n\n"
            f"AGENT TRACE:\n{_trace_digest(trace)}\n\n"
            "For every rule, score 0-100 for adherence. Return JSON:\n"
            "{\n"
            '  "results": [\n'
            '    {"rule_id": string, "score": int, "pass": bool, "confidence": float,\n'
            '     "reasoning": string, "violations": [string], "recommendations": [string]}\n'
            "  ]\n"
            "}\n"
            "Include exactly one object per rule id listed above."
        )
        data = self.backend.complete_json(user, system=JUDGE_SYSTEM, model=self.model)
        degraded_all = "_llm_error" in data
        latency = float(data.get("_llm_latency_ms", 0.0) or 0.0)
        model = str(data.get("_llm_model", ""))

        by_id: Dict[str, Dict[str, Any]] = {}
        for item in data.get("results", []) or []:
            if isinstance(item, dict) and item.get("rule_id"):
                by_id[str(item["rule_id"])] = item

        out: Dict[str, JudgeResult] = {}
        for rule in rules:
            item = by_id.get(rule.id)
            if item is None:
                item = {
                    "_llm_error": data.get("_llm_error", "rule missing from batch result"),
                }
            item.setdefault("_llm_latency_ms", latency)
            item.setdefault("_llm_model", model)
            out[rule.id] = self._to_result(item, pass_floor=60.0)
        return out

    # ── reasoning quality ──────────────────────────────────────────────────
    def score_reasoning(self, reasoning_text: str, task_prompt: str) -> JudgeResult:
        user = (
            "Evaluate the logical quality of an AI agent's chain-of-thought.\n\n"
            f"TASK:\n{_truncate(task_prompt, 1500)}\n\n"
            f"AGENT REASONING:\n{_truncate(reasoning_text, 4000)}\n\n"
            "Check for: circular reasoning, non-sequiturs, unsupported leaps, and "
            "whether conclusions follow from premises. Return JSON:\n"
            '{"score": int, "pass": bool, "confidence": float, "reasoning": string, '
            '"violations": [string], "recommendations": [string]}'
        )
        data = self.backend.complete_json(user, system=JUDGE_SYSTEM, model=self.model)
        return self._to_result(data, pass_floor=60.0)

    # ── semantic task completion ────────────────────────────────────────────
    def judge_task_completion(
        self,
        task_prompt: str,
        response: str,
        expected_contains: List[str],
        expected_excludes: List[str],
    ) -> JudgeResult:
        user = (
            "Judge whether an AI agent completed the task, allowing for semantic "
            "equivalence even if wording/format differs.\n\n"
            f"TASK:\n{_truncate(task_prompt, 2000)}\n\n"
            f"AGENT RESPONSE:\n{_truncate(response)}\n\n"
            f"Should address (semantically): {expected_contains}\n"
            f"Must NOT contain: {expected_excludes}\n\n"
            "Return JSON with score = completion percentage 0-100:\n"
            '{"score": int, "pass": bool, "confidence": float, "reasoning": string, '
            '"violations": [string], "recommendations": [string]}'
        )
        data = self.backend.complete_json(user, system=JUDGE_SYSTEM, model=self.model)
        return self._to_result(data, pass_floor=70.0)

    # ── tone analysis ────────────────────────────────────────────────────────
    def analyze_tone(self, response: str, expected_tone: str) -> JudgeResult:
        user = (
            f"Assess whether this response matches a '{expected_tone}' tone.\n\n"
            f"RESPONSE:\n{_truncate(response, 3000)}\n\n"
            "Return JSON (score 0-100 for tone match):\n"
            '{"score": int, "pass": bool, "confidence": float, "reasoning": string, '
            '"violations": [string], "recommendations": [string]}'
        )
        data = self.backend.complete_json(user, system=JUDGE_SYSTEM, model=self.model)
        return self._to_result(data, pass_floor=60.0)

    # ── generic adequacy judge (used e.g. for remediation presence) ──────────
    def assess(self, instruction: str, response: str) -> JudgeResult:
        user = (
            f"{instruction}\n\n"
            f"AGENT RESPONSE:\n{_truncate(response)}\n\n"
            "Return JSON (score 0-100):\n"
            '{"score": int, "pass": bool, "confidence": float, "reasoning": string, '
            '"violations": [string], "recommendations": [string]}'
        )
        data = self.backend.complete_json(user, system=JUDGE_SYSTEM, model=self.model)
        return self._to_result(data, pass_floor=60.0)

    # ── memory retention (multi-turn) ────────────────────────────────────────
    def judge_memory_retention(self, conversation: str) -> JudgeResult:
        user = (
            "Evaluate whether the assistant retained and correctly used context "
            "established earlier in this multi-turn conversation.\n\n"
            f"CONVERSATION:\n{_truncate(conversation, 6000)}\n\n"
            "Penalize re-asking for known info or contradicting earlier turns. "
            "Return JSON (score 0-100 for retention):\n"
            '{"score": int, "pass": bool, "confidence": float, "reasoning": string, '
            '"violations": [string], "recommendations": [string]}'
        )
        data = self.backend.complete_json(user, system=JUDGE_SYSTEM, model=self.model)
        return self._to_result(data, pass_floor=60.0)

    # ── report narration ─────────────────────────────────────────────────────
    def generate_executive_summary(self, report_context: str, model: Optional[str] = None) -> str:
        user = (
            "You are writing the executive summary of an AI agent evaluation "
            "report for a technical leadership audience. Given the results below, "
            "write ONE professional, actionable paragraph (4-7 sentences): the "
            "verdict, the strongest and weakest areas, and the headline risk. Do "
            "not use markdown headers or bullet points.\n\n"
            f"RESULTS:\n{_truncate(report_context, 6000)}"
        )
        res = self.backend.complete(
            user,
            system="You are a precise, professional technical writer.",
            model=model or self.model,
        )
        if res.failed:
            return (
                "(Executive summary unavailable — LLM narration failed. "
                "See metric scores and rule results below.)"
            )
        return res.text.strip()

    def generate_recommendations(self, report_context: str, model: Optional[str] = None) -> List[str]:
        data = self.backend.complete_json(
            "Based on this AI agent evaluation, produce prioritized, concrete "
            "engineering action items to improve the agent. Return JSON: "
            '{"recommendations": [string, ...]} with 3-6 items, most impactful '
            "first.\n\nRESULTS:\n" + _truncate(report_context, 6000),
            system="You are a precise, professional technical writer.",
            model=model or self.model,
        )
        recs = data.get("recommendations")
        if isinstance(recs, list) and recs:
            return [str(r) for r in recs]
        return ["(No recommendations generated — LLM narration failed.)"]

    # ── internal ─────────────────────────────────────────────────────────────
    def _to_result(self, data: Dict[str, Any], pass_floor: float) -> JudgeResult:
        degraded = "_llm_error" in data
        score = _coerce_score(data.get("score"), default=50.0 if degraded else 50.0)
        passed = data.get("pass")
        if not isinstance(passed, bool):
            passed = score >= pass_floor
        confidence = _coerce_float(data.get("confidence"), default=0.3 if degraded else 0.6)
        reasoning = str(data.get("reasoning") or ("LLM evaluation unavailable" if degraded else ""))
        violations = _coerce_str_list(data.get("violations"))
        recommendations = _coerce_str_list(data.get("recommendations"))
        if degraded and not violations:
            violations = [f"LLM evaluation degraded: {data.get('_llm_error')}"]
        return JudgeResult(
            score=score,
            passed=bool(passed),
            confidence=confidence,
            reasoning=reasoning,
            violations=violations,
            recommendations=recommendations,
            degraded=degraded,
            latency_ms=float(data.get("_llm_latency_ms", 0.0) or 0.0),
            model=str(data.get("_llm_model", "")),
        )


# ── coercion helpers (defensive against loose model output) ───────────────────
def _coerce_score(v: Any, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(100.0, f))


def _coerce_float(v: Any, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))


def _coerce_str_list(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []
