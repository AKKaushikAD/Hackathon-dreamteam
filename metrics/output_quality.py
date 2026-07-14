"""Output quality metric — schema/format compliance, required fields, tone, length."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core.llm_client import extract_json
from core.playbook_loader import OutputFormat
from metrics.base_metric import BaseMetric, MetricContext, MetricResult, Violation


class OutputQualityMetric(BaseMetric):
    name = "output_quality"
    evaluation_type = "hybrid"

    def evaluate(self, ctx: MetricContext) -> MetricResult:
        req = ctx.playbook.output_requirements
        response = ctx.response or ""
        violations: List[Violation] = []
        subscores: Dict[str, float] = {}

        # ── format / schema compliance ──────────────────────────────────────
        parsed: Optional[Dict[str, Any]] = None
        if req.format == OutputFormat.json:
            parsed = extract_json(response)
            if parsed is None:
                try:
                    parsed = json.loads(response)
                except Exception:
                    parsed = None
            if parsed is None:
                subscores["format"] = 0.0
                violations.append(
                    Violation(description="Output is not valid JSON.", critical=False)
                )
            else:
                subscores["format"] = 100.0
        else:
            subscores["format"] = 100.0  # non-JSON formats: presence is enough here

        # ── required fields ─────────────────────────────────────────────────
        if req.required_fields:
            present, missing = self._check_fields(req.required_fields, parsed, response)
            field_score = 100.0 * (len(present) / len(req.required_fields))
            subscores["required_fields"] = round(field_score, 2)
            if missing:
                violations.append(
                    Violation(description=f"Missing required fields: {missing}", critical=False)
                )
        else:
            subscores["required_fields"] = 100.0
            missing = []

        # ── length compliance ────────────────────────────────────────────────
        length = len(response)
        if length <= req.max_length:
            subscores["length"] = 100.0
        else:
            over = length - req.max_length
            subscores["length"] = max(0.0, 100.0 - (over / max(1, req.max_length)) * 100.0)
            violations.append(
                Violation(
                    description=f"Output length {length} exceeds max_length {req.max_length}.",
                    critical=False,
                )
            )

        # ── readability (simple heuristic) ────────────────────────────────────
        subscores["readability"] = self._readability(response)

        # ── tone (LLM judge) ──────────────────────────────────────────────────
        tone_res = ctx.judge.analyze_tone(response, req.tone.value)
        subscores["tone"] = round(tone_res.score, 2)
        if tone_res.score < 60:
            violations.append(
                Violation(
                    description=f"Tone does not match '{req.tone.value}': {tone_res.reasoning[:160]}",
                    critical=False,
                )
            )

        score = (
            0.30 * subscores["format"]
            + 0.25 * subscores["required_fields"]
            + 0.20 * subscores["tone"]
            + 0.15 * subscores["length"]
            + 0.10 * subscores["readability"]
        )
        details = {
            "subscores": subscores,
            "format_expected": req.format.value,
            "parsed_ok": parsed is not None,
            "missing_fields": missing,
            "length": length,
            "max_length": req.max_length,
            "tone_expected": req.tone.value,
            "tone_reasoning": tone_res.reasoning,
        }
        return self._result(
            self.name, score, self.weight, details=details,
            violations=violations, pass_floor=70.0,
        )

    @staticmethod
    def _check_fields(fields, parsed, response):
        present, missing = [], []
        low = response.lower()
        for f in fields:
            if isinstance(parsed, dict) and f in parsed:
                present.append(f)
            elif f.lower() in low:  # lenient fallback for non-JSON
                present.append(f)
            else:
                missing.append(f)
        return present, missing

    @staticmethod
    def _readability(text: str) -> float:
        if not text.strip():
            return 0.0
        words = text.split()
        if not words:
            return 0.0
        sentences = max(1, text.count(".") + text.count("!") + text.count("?"))
        avg_sentence_len = len(words) / sentences
        # Prefer 8-25 words/sentence; penalize extremes.
        if 8 <= avg_sentence_len <= 25:
            return 100.0
        if avg_sentence_len < 8:
            return max(60.0, 100.0 - (8 - avg_sentence_len) * 5)
        return max(50.0, 100.0 - (avg_sentence_len - 25) * 2)
