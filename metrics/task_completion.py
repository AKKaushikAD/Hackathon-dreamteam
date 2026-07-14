"""Task completion metric — goal achievement (deterministic + LLM semantic)."""

from __future__ import annotations

from typing import List

from metrics.base_metric import BaseMetric, MetricContext, MetricResult, Violation


class TaskCompletionMetric(BaseMetric):
    name = "task_completion"
    evaluation_type = "hybrid"

    def evaluate(self, ctx: MetricContext) -> MetricResult:
        response = ctx.response or ""
        low = response.lower()
        scenario = ctx.scenario

        # ── deterministic keyword coverage (partial credit) ────────────────
        contains = scenario.expected_output_contains
        excludes = scenario.expected_output_excludes
        hits = [kw for kw in contains if kw.lower() in low]
        missing = [kw for kw in contains if kw.lower() not in low]
        leaked = [kw for kw in excludes if kw.lower() in low]

        coverage = (len(hits) / len(contains)) if contains else 1.0
        det_score = 100.0 * coverage
        if leaked:
            det_score *= 0.4  # heavy penalty for producing excluded content

        # ── LLM semantic completion (format-independent) ───────────────────
        judge = ctx.judge.judge_task_completion(scenario.prompt, response, contains, excludes)
        sem_score = judge.score

        # Blend: semantic judgment leads, deterministic keeps it grounded.
        score = 0.6 * sem_score + 0.4 * det_score
        if leaked:
            score = min(score, 45.0)

        violations: List[Violation] = []
        if leaked:
            violations.append(
                Violation(
                    description=f"Output contains excluded content: {leaked}",
                    critical=False,
                )
            )
        if missing and coverage < 0.5:
            violations.append(
                Violation(description=f"Missing expected content: {missing}", critical=False)
            )

        details = {
            "completion_pct": round(score, 2),
            "semantic_score": round(sem_score, 2),
            "keyword_coverage": round(coverage, 3),
            "matched_keywords": hits,
            "missing_keywords": missing,
            "leaked_excluded": leaked,
            "claude_reasoning": judge.reasoning,
            "degraded": judge.degraded,
        }
        return self._result(
            self.name, score, self.weight, details=details,
            violations=violations, pass_floor=70.0,
        )
