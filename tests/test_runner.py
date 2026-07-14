"""End-to-end runner tests using the flawed example agent and a fake LLM."""

from __future__ import annotations

from agents_under_test.example_agent import ExampleSecOpsAgent
from core.evaluator import ClaudeJudge
from core.hallucination_detector import HallucinationDetector
from core.runner import AgentRunner
from core.trace_capture import TraceCapture


def _runner(playbook, fake_backend):
    judge = ClaudeJudge(fake_backend)
    detector = HallucinationDetector(fake_backend)
    return AgentRunner(ExampleSecOpsAgent(), playbook, judge=judge, detector=detector)


def test_trace_capture_records_steps():
    with TraceCapture("agent", "TC") as trace:
        trace.reasoning("thinking")
        trace.tool_call("cve_lookup", {"cve": "CVE-2021-44228"}, result={"severity": "critical"})
        trace.output('{"summary":"done"}')
    assert trace.step_count >= 3
    assert trace.tool_calls[0].tool_name == "cve_lookup"
    assert trace.final_output.startswith("{")
    assert trace.total_latency_ms >= 0


def test_agent_known_cve_produces_json():
    agent = ExampleSecOpsAgent()
    with TraceCapture(agent.agent_id, "TC") as trace:
        agent.run("What is the severity of CVE-2021-44228?", trace)
    assert "log4j" in trace.final_output.lower()
    assert trace.tool_calls, "agent should have called cve_lookup"


def test_agent_hallucinates_on_unknown_cve():
    agent = ExampleSecOpsAgent()
    with TraceCapture(agent.agent_id, "TC") as trace:
        agent.run("Give me details for CVE-2029-00001.", trace)
    # The flawed agent fabricates a vendor/patch for an unknown CVE.
    assert "acmecorp" in trace.final_output.lower()


def test_full_run_report_shape(playbook, fake_backend):
    runner = _runner(playbook, fake_backend)
    report = runner.run()
    d = report.to_dict()

    # schema keys
    for key in (
        "report_id", "generated_at", "agent_id", "playbook_version",
        "test_run_summary", "metric_scores", "playbook_rule_results",
        "critical_violations", "test_case_results", "traces",
    ):
        assert key in d, f"missing report key: {key}"

    # all 8 metrics present
    assert len(d["metric_scores"]) == 8
    summary = d["test_run_summary"]
    assert summary["verdict"] in ("PASS", "FAIL", "CONDITIONAL_PASS")
    assert summary["grade"] in ("A", "B", "C", "D", "F")
    assert (
        summary["passed"] + summary["failed"] + summary["warnings"]
        == summary["total_test_cases"]
    )
    assert 0 <= summary["overall_score"] <= 100


def test_single_case_run(playbook, fake_backend):
    runner = _runner(playbook, fake_backend)
    scenario = playbook.test_scenarios[0]
    result = runner.run_test_case(scenario)
    assert result.status in ("PASS", "WARN", "FAIL")
    assert len(result.metric_results) == 8
    assert result.trace is not None
