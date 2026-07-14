"""Efficiency metric — latency, step count, and token efficiency (deterministic)."""

from __future__ import annotations

from typing import List

from metrics.base_metric import BaseMetric, MetricContext, MetricResult, Violation


class EfficiencyMetric(BaseMetric):
    name = "efficiency"
    evaluation_type = "deterministic"

    def evaluate(self, ctx: MetricContext) -> MetricResult:
        thresholds = ctx.playbook.performance_thresholds
        trace = ctx.trace
        violations: List[Violation] = []

        actual_steps = max(1, trace.step_count)
        # Minimum steps: default to the playbook hint, else a sensible floor.
        min_steps = ctx.shared.get("min_steps") or self._infer_min_steps(ctx)
        step_score = 100.0 * (min_steps / actual_steps)
        step_score = max(0.0, min(100.0, step_score))
        if actual_steps > thresholds.max_steps:
            violations.append(
                Violation(
                    description=f"Used {actual_steps} steps (max {thresholds.max_steps}).",
                    critical=False,
                )
            )

        # ── latency ─────────────────────────────────────────────────────────
        latency_s = trace.total_latency_ms / 1000.0
        max_latency = max(0.001, thresholds.max_latency_seconds)
        latency_score = max(0.0, min(100.0, 100.0 * (1.0 - max(0.0, latency_s - max_latency) / max_latency)))
        if latency_s > max_latency:
            violations.append(
                Violation(
                    description=f"Latency {latency_s:.1f}s exceeds max {max_latency:.1f}s.",
                    critical=False,
                )
            )

        # ── token efficiency (output / total) ────────────────────────────────
        out_tokens = sum(s.token_count for s in trace.steps if s.step_type == "output")
        total_tokens = max(1, trace.total_tokens_used)
        token_ratio = min(1.0, out_tokens / total_tokens) if total_tokens else 1.0
        token_score = 100.0 * token_ratio if out_tokens else 100.0

        score = 0.45 * step_score + 0.35 * latency_score + 0.20 * token_score
        details = {
            "actual_steps": actual_steps,
            "min_steps_estimate": min_steps,
            "max_steps": thresholds.max_steps,
            "step_score": round(step_score, 2),
            "latency_seconds": round(latency_s, 3),
            "max_latency_seconds": max_latency,
            "latency_score": round(latency_score, 2),
            "output_tokens": out_tokens,
            "total_tokens": trace.total_tokens_used,
            "token_efficiency_score": round(token_score, 2),
        }
        return self._result(
            self.name, score, self.weight, details=details,
            violations=violations, pass_floor=60.0,
        )

    @staticmethod
    def _infer_min_steps(ctx: MetricContext) -> int:
        """Heuristic minimum: one reasoning + required tools + one output."""
        tc = ctx.playbook.tool_constraints
        required = 0
        if ctx.scenario.task_type:
            required = len(tc.required_tools_for_task_types.get(ctx.scenario.task_type, []))
        # reasoning(1) + required tool calls (+ their results) + output(1)
        return max(2, 1 + required * 2 + 1)
