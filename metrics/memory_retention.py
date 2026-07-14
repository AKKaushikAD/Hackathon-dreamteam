"""Memory retention metric — context consistency across multi-turn tests."""

from __future__ import annotations

from metrics.base_metric import BaseMetric, MetricContext, MetricResult, Violation


class MemoryRetentionMetric(BaseMetric):
    name = "memory_retention"
    evaluation_type = "llm"

    def evaluate(self, ctx: MetricContext) -> MetricResult:
        conversation = ctx.conversation
        # Only meaningful for multi-turn conversations.
        if not conversation:
            # Detect context-window-overflow handling gracefully: if the trace
            # recorded memory ops, lightly reward it; otherwise mark N/A at 100.
            mem_ops = len(ctx.trace.memory_reads) + len(ctx.trace.memory_writes)
            return self._result(
                self.name,
                100.0,
                self.weight,
                details={
                    "applicable": False,
                    "note": "single-turn test — memory retention not exercised",
                    "memory_ops_in_trace": mem_ops,
                },
                pass_floor=0.0,
            )

        res = ctx.judge.judge_memory_retention(conversation)
        violations = []
        if res.violations:
            violations.append(
                Violation(
                    description="Context retention issues: " + "; ".join(res.violations[:3]),
                    critical=False,
                )
            )
        details = {
            "applicable": True,
            "score": round(res.score, 2),
            "claude_reasoning": res.reasoning,
            "issues": res.violations,
            "degraded": res.degraded,
        }
        return self._result(
            self.name, res.score, self.weight, details=details,
            violations=violations, pass_floor=60.0,
        )
