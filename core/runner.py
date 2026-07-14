"""Agent Runner Engine.

Executes an agent against every scenario in a playbook, runs all metrics per
test case, and aggregates everything into an ``EvaluationReport`` whose
``to_dict()`` matches the framework's canonical JSON report schema.

LLM calls (via the Claude CLI) are blocking subprocess calls, so test cases are
parallelised with a thread pool bounded by ``config.max_concurrent_tests``.
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from config import CONFIG, FrameworkConfig
from core.evaluator import ClaudeJudge
from core.hallucination_detector import HallucinationDetector
from core.playbook_loader import Playbook, TestScenario
from core.trace_capture import AgentTrace, TraceCapture
from metrics import build_metrics
from metrics.base_metric import MetricContext, MetricResult, Violation


@dataclass
class TestCaseResult:
    test_case_id: str
    name: str
    prompt: str
    output: str
    weighted_score: float
    status: str                                    # PASS | WARN | FAIL
    metric_results: Dict[str, MetricResult] = field(default_factory=dict)
    critical_violations: List[Violation] = field(default_factory=list)
    trace: Optional[AgentTrace] = None
    error: Optional[str] = None

    def to_dict(self, include_trace: bool = False) -> Dict[str, Any]:
        d = {
            "test_case_id": self.test_case_id,
            "name": self.name,
            "prompt": self.prompt,
            "output": self.output,
            "weighted_score": round(self.weighted_score, 2),
            "status": self.status,
            "error": self.error,
            "metrics": {k: v.to_dict() for k, v in self.metric_results.items()},
            "critical_violations": [v.to_dict() for v in self.critical_violations],
        }
        if include_trace and self.trace is not None:
            d["trace"] = self.trace.to_dict()
        return d


@dataclass
class EvaluationReport:
    report_id: str
    generated_at: str
    agent_id: str
    playbook_version: str
    overall_score: float
    grade: str
    verdict: str
    test_case_results: List[TestCaseResult]
    metric_scores: Dict[str, Dict[str, Any]]
    playbook_rule_results: List[Dict[str, Any]]
    critical_violations: List[Dict[str, Any]]
    executive_summary: str = ""
    recommendations: List[str] = field(default_factory=list)

    @property
    def counts(self) -> Dict[str, int]:
        passed = sum(1 for r in self.test_case_results if r.status == "PASS")
        warned = sum(1 for r in self.test_case_results if r.status == "WARN")
        failed = sum(1 for r in self.test_case_results if r.status == "FAIL")
        return {"passed": passed, "warnings": warned, "failed": failed}

    def to_dict(self) -> Dict[str, Any]:
        counts = self.counts
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "agent_id": self.agent_id,
            "playbook_version": self.playbook_version,
            "test_run_summary": {
                "total_test_cases": len(self.test_case_results),
                "passed": counts["passed"],
                "failed": counts["failed"],
                "warnings": counts["warnings"],
                "overall_score": round(self.overall_score, 2),
                "grade": self.grade,
                "verdict": self.verdict,
            },
            "metric_scores": self.metric_scores,
            "playbook_rule_results": self.playbook_rule_results,
            "critical_violations": self.critical_violations,
            "test_case_results": [r.to_dict() for r in self.test_case_results],
            "executive_summary": self.executive_summary,
            "recommendations": self.recommendations,
            "traces": [
                r.trace.to_dict()
                for r in self.test_case_results
                if r.trace is not None and r.status == "FAIL"
            ],
        }


class AgentRunner:
    """Runs an agent through a playbook and produces an ``EvaluationReport``."""

    def __init__(
        self,
        agent,
        playbook: Playbook,
        config: Optional[FrameworkConfig] = None,
        judge: Optional[ClaudeJudge] = None,
        detector: Optional[HallucinationDetector] = None,
        progress: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.agent = agent
        self.playbook = playbook
        self.config = config or CONFIG
        self.judge = judge or ClaudeJudge(model=self.config.judge_model)
        self.detector = detector or HallucinationDetector(model=self.config.judge_model)
        self.metrics = build_metrics(self.config)
        self._progress = progress or (lambda ev, msg: None)

    # ── single test case ─────────────────────────────────────────────────
    def run_test_case(self, scenario: TestScenario) -> TestCaseResult:
        self._progress("case_start", scenario.id)

        # 1) execute the agent, capturing the full trace (crash-safe).
        with TraceCapture(self.agent.agent_id, scenario.id) as trace:
            self.agent.run(scenario.prompt, trace, scenario)
        output = trace.final_output

        # 2) run every metric.
        ctx = MetricContext(
            trace=trace,
            scenario=scenario,
            playbook=self.playbook,
            judge=self.judge,
            detector=self.detector,
            config=self.config,
            conversation=None,  # single-turn by default; multi-turn set by caller
        )
        metric_results: Dict[str, MetricResult] = {}
        for metric in self.metrics:
            metric_results[metric.name] = metric.run(ctx)

        # 3) score & status for this case.
        weighted = sum(m.score * m.weight for m in metric_results.values())
        criticals = [
            v for m in metric_results.values() for v in m.violations if v.critical
        ]
        if trace.error:
            criticals.append(Violation(f"Agent crashed: {trace.error}", critical=True))
        status = self._case_status(weighted, criticals, metric_results)

        self._progress("case_done", f"{scenario.id} [{status}] {weighted:.0f}")
        return TestCaseResult(
            test_case_id=scenario.id,
            name=scenario.name or scenario.id,
            prompt=scenario.prompt,
            output=output,
            weighted_score=weighted,
            status=status,
            metric_results=metric_results,
            critical_violations=criticals,
            trace=trace,
            error=trace.error,
        )

    # ── full run ────────────────────────────────────────────────────────
    def run(self, scenario_ids: Optional[List[str]] = None) -> EvaluationReport:
        scenarios = self.playbook.test_scenarios
        if scenario_ids:
            wanted = set(scenario_ids)
            scenarios = [s for s in scenarios if s.id in wanted]
        if not scenarios:
            raise ValueError("No test scenarios to run.")

        results: List[TestCaseResult] = []
        max_workers = max(1, self.config.max_concurrent_tests)
        if max_workers == 1:
            for sc in scenarios:
                results.append(self.run_test_case(sc))
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(self.run_test_case, sc): sc for sc in scenarios}
                for fut in as_completed(futures):
                    results.append(fut.result())
        # keep deterministic scenario order
        order = {s.id: i for i, s in enumerate(scenarios)}
        results.sort(key=lambda r: order.get(r.test_case_id, 0))

        return self._assemble_report(results)

    # ── aggregation ───────────────────────────────────────────────────────
    def _assemble_report(self, results: List[TestCaseResult]) -> EvaluationReport:
        metric_names = [m.name for m in self.metrics]
        weights = {m.name: m.weight for m in self.metrics}

        # per-metric average across test cases
        metric_scores: Dict[str, Dict[str, Any]] = {}
        for name in metric_names:
            scores = [r.metric_results[name].score for r in results if name in r.metric_results]
            avg = sum(scores) / len(scores) if scores else 0.0
            metric_scores[name] = {
                "score": round(avg, 2),
                "weight": round(weights.get(name, 0.0), 4),
                "details": {
                    "per_case": {
                        r.test_case_id: r.metric_results[name].to_dict()
                        for r in results
                        if name in r.metric_results
                    }
                },
            }

        overall = sum(metric_scores[n]["score"] * weights.get(n, 0.0) for n in metric_names)

        # critical violations (flattened with test-case + step attribution)
        critical_violations: List[Dict[str, Any]] = []
        for r in results:
            for v in r.critical_violations:
                critical_violations.append(
                    {
                        "rule_id": v.rule_id,
                        "test_case_id": r.test_case_id,
                        "description": v.description,
                        "trace_step": v.trace_step,
                    }
                )

        rule_results = self._aggregate_rules(results)

        has_critical = bool(critical_violations)
        grade = self.config.grade_for_score(overall)
        verdict = self._verdict(overall, has_critical)

        return EvaluationReport(
            report_id=f"report_{uuid.uuid4().hex[:12]}",
            generated_at=datetime.now(timezone.utc).isoformat(),
            agent_id=self.agent.agent_id,
            playbook_version=self.playbook.version,
            overall_score=overall,
            grade=grade,
            verdict=verdict,
            test_case_results=results,
            metric_scores=metric_scores,
            playbook_rule_results=rule_results,
            critical_violations=critical_violations,
        )

    def _aggregate_rules(self, results: List[TestCaseResult]) -> List[Dict[str, Any]]:
        """Roll up per-rule adherence across all test cases."""
        acc: Dict[str, Dict[str, Any]] = {}
        for rule in self.playbook.rules:
            acc[rule.id] = {
                "rule_id": rule.id,
                "rule_name": rule.name,
                "priority": rule.priority.value,
                "_scores": [],
                "violations": [],
                "_reasonings": [],
                "_fail": False,
                "_critical_fail": False,
            }
        for r in results:
            pa = r.metric_results.get("playbook_adherence")
            if not pa:
                continue
            for entry in pa.details.get("per_rule", []):
                rid = entry["rule_id"]
                bucket = acc.get(rid)
                if bucket is None:
                    continue
                bucket["_scores"].append(entry["score"])
                bucket["_reasonings"].append(entry.get("claude_reasoning", ""))
                if not entry.get("pass", True):
                    bucket["_fail"] = True
                    for v in entry.get("violations", []) or [f"score {entry['score']:.0f}"]:
                        bucket["violations"].append(
                            {"test_case_id": r.test_case_id, "description": v}
                        )
            for v in r.critical_violations:
                if v.rule_id in acc:
                    acc[v.rule_id]["_critical_fail"] = True

        out: List[Dict[str, Any]] = []
        for rid, b in acc.items():
            scores = b["_scores"]
            avg = sum(scores) / len(scores) if scores else 100.0
            if b["_critical_fail"]:
                status = "FAIL"
            elif b["_fail"] or avg < 60:
                status = "FAIL" if avg < 60 else "WARN"
            else:
                status = "PASS"
            out.append(
                {
                    "rule_id": rid,
                    "rule_name": b["rule_name"],
                    "priority": b["priority"],
                    "status": status,
                    "score": round(avg, 2),
                    "violations": b["violations"],
                    "claude_reasoning": next(
                        (x for x in b["_reasonings"] if x), ""
                    ),
                }
            )
        return out

    def _case_status(
        self, weighted: float, criticals: List[Violation], metrics: Dict[str, MetricResult]
    ) -> str:
        if criticals and self.config.critical_violation_auto_fail:
            return "FAIL"
        if weighted < 60:
            return "FAIL"
        non_critical = any(v for m in metrics.values() for v in m.violations)
        if weighted < 70 or non_critical:
            return "WARN"
        return "PASS"

    def _verdict(self, overall: float, has_critical: bool) -> str:
        if has_critical and self.config.critical_violation_auto_fail:
            return "FAIL"
        if overall < 60:
            return "FAIL"
        if overall < 80:
            return "CONDITIONAL_PASS"
        return "PASS"
