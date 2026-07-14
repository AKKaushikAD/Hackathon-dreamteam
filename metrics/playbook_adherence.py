"""Playbook adherence metric — per-rule compliance scoring.

Checks each playbook rule individually using the appropriate method:
  * ``llm_judge`` rules  -> ClaudeJudge.evaluate_rule
  * ``deterministic``    -> ClaudeJudge.evaluate_rule with trace evidence
                           (deterministic tool/output checks also feed in via
                            the dedicated ToolUsage/OutputQuality metrics)
  * ``hybrid``           -> LLM judge blended with the hallucination signal when
                            the rule concerns fabrication

Returns an overall weighted adherence % plus per-rule detail, and flags any
critical-rule violation separately (which triggers auto-fail downstream).
"""

from __future__ import annotations

from typing import Dict, List

from core.playbook_loader import EvaluationType, Rule
from metrics.base_metric import BaseMetric, MetricContext, MetricResult, Violation

_HALLUCINATION_KEYWORDS = ("halluc", "fabricat", "invent", "made-up", "made up", "cite", "citation")


class PlaybookAdherenceMetric(BaseMetric):
    name = "playbook_adherence"
    evaluation_type = "hybrid"

    def evaluate(self, ctx: MetricContext) -> MetricResult:
        rules = ctx.playbook.rules
        if not rules:
            return self._result(
                self.name, 100.0, self.weight,
                details={"note": "no rules defined"}, pass_floor=0.0,
            )

        per_rule: List[Dict] = []
        violations: List[Violation] = []
        weighted_sum = 0.0
        weight_total = 0.0

        # Score every rule in ONE batched LLM call (instead of one call per rule).
        batch = ctx.judge.evaluate_rules_batch(rules, ctx.response, ctx.trace)

        for rule in rules:
            judge_res = batch[rule.id]
            score = judge_res.score

            # Hybrid rules about fabrication: blend in the hallucination signal.
            if rule.evaluation_type == EvaluationType.hybrid and self._is_hallucination_rule(rule):
                halluc_rate = self._hallucination_rate(ctx)
                halluc_score = max(0.0, 100.0 * (1.0 - halluc_rate))
                score = 0.5 * score + 0.5 * halluc_score

            rule_weight = max(rule.weight, 0.01)
            weighted_sum += score * rule_weight
            weight_total += rule_weight

            rule_passed = judge_res.passed and score >= 60.0
            if rule.is_critical and not rule_passed:
                violations.append(
                    Violation(
                        description=f"CRITICAL rule {rule.id} ({rule.name}) violated: "
                        + "; ".join(judge_res.violations[:3] or [judge_res.reasoning[:160]]),
                        critical=True,
                        rule_id=rule.id,
                    )
                )
            elif not rule_passed:
                violations.append(
                    Violation(
                        description=f"Rule {rule.id} ({rule.name}) not satisfied "
                        f"(score {score:.0f}).",
                        critical=False,
                        rule_id=rule.id,
                    )
                )

            per_rule.append(
                {
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "priority": rule.priority.value,
                    "evaluation_type": rule.evaluation_type.value,
                    "score": round(score, 2),
                    "pass": rule_passed,
                    "confidence": round(judge_res.confidence, 3),
                    "claude_reasoning": judge_res.reasoning,
                    "violations": judge_res.violations,
                    "recommendations": judge_res.recommendations,
                    "degraded": judge_res.degraded,
                }
            )

        overall = weighted_sum / weight_total if weight_total else 100.0
        details = {
            "overall_adherence_pct": round(overall, 2),
            "rules_evaluated": len(rules),
            "rules_passed": sum(1 for r in per_rule if r["pass"]),
            "critical_violations": sum(1 for v in violations if v.critical),
            "per_rule": per_rule,
        }
        return self._result(
            self.name, overall, self.weight,
            details=details, violations=violations, pass_floor=70.0,
        )

    @staticmethod
    def _is_hallucination_rule(rule: Rule) -> bool:
        blob = f"{rule.name} {rule.description} {rule.check_method}".lower()
        return any(k in blob for k in _HALLUCINATION_KEYWORDS)

    @staticmethod
    def _hallucination_rate(ctx: MetricContext) -> float:
        report = ctx.shared.get("hallucination_report")
        if report is None:
            report = ctx.detector.detect(
                ctx.response, domain=ctx.playbook.domain, trace=ctx.trace
            )
            ctx.shared["hallucination_report"] = report
        return report.hallucination_rate
