"""Reasoning quality metric — chain-of-thought validity (LLM-scored)."""

from __future__ import annotations

from metrics.base_metric import BaseMetric, MetricContext, MetricResult, Violation


class ReasoningQualityMetric(BaseMetric):
    name = "reasoning_quality"
    evaluation_type = "llm"

    def evaluate(self, ctx: MetricContext) -> MetricResult:
        reasoning = ctx.trace.reasoning_text()
        if not reasoning.strip():
            # No explicit reasoning trace — fall back to judging the output itself
            # for internal coherence, but cap the score since CoT wasn't exposed.
            reasoning = ctx.response
            capped = True
        else:
            capped = False

        res = ctx.judge.score_reasoning(reasoning, ctx.scenario.prompt)
        score = res.score
        if capped:
            score = min(score, 80.0)

        violations = []
        if res.violations:
            violations.append(
                Violation(
                    description="Reasoning issues: " + "; ".join(res.violations[:3]),
                    critical=False,
                )
            )

        details = {
            "score": round(score, 2),
            "confidence": round(res.confidence, 3),
            "claude_reasoning": res.reasoning,
            "issues": res.violations,
            "recommendations": res.recommendations,
            "reasoning_steps_present": not capped,
            "degraded": res.degraded,
        }
        return self._result(
            self.name, score, self.weight, details=details,
            violations=violations, pass_floor=60.0,
        )
