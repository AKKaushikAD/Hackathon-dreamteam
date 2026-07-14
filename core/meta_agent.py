"""Meta-Agent orchestrator (Claude-powered).

The Meta-Agent is itself driven by Claude (via the CLI). It:
  * reads the playbook,
  * decides which test cases to run (LLM planning, with a safe fallback),
  * delegates execution to the Runner and specialized evaluators,
  * interprets results,
  * synthesizes a final verdict + narrative report.

It exposes a tool surface the planner can reason over. Because the inference
engine is the headless Claude CLI (not the tool-calling HTTP API), tool
"invocation" is orchestrated in Python: the planner proposes a JSON plan naming
tools/arguments, and the Meta-Agent dispatches those tools deterministically.
This keeps orchestration auditable while still being LLM-directed.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from config import CONFIG, FrameworkConfig
from core.evaluator import ClaudeJudge
from core.hallucination_detector import HallucinationDetector
from core.llm_client import DEFAULT_BACKEND, LLMBackend
from core.playbook_loader import Playbook
from core.runner import AgentRunner, EvaluationReport
from core.trace_capture import AgentTrace

META_SYSTEM = (
    "You are the Meta-Agent orchestrator of an AI agent evaluation framework. "
    "You plan and coordinate the testing of another AI agent against a structured "
    "playbook, delegate to specialized evaluation tools, and synthesize a final, "
    "unbiased verdict. You are rigorous, security-minded, and concise. Return "
    "structured JSON when asked."
)

# The tool surface available to the Meta-Agent (documented for the planner).
TOOL_SPECS: List[Dict[str, Any]] = [
    {
        "name": "get_playbook_rules",
        "description": "Return the playbook's rules for the given agent_id.",
        "input_schema": {"agent_id": "string"},
    },
    {
        "name": "run_test_case",
        "description": "Execute the agent on one test case and capture its trace.",
        "input_schema": {"agent_id": "string", "test_case_id": "string", "prompt": "string"},
    },
    {
        "name": "evaluate_trace",
        "description": "Score a captured trace against a specific playbook rule.",
        "input_schema": {"trace_id": "string", "rule_id": "string"},
    },
    {
        "name": "check_hallucination",
        "description": "Run the Claude hallucination detector over some text.",
        "input_schema": {"text": "string", "domain": "string"},
    },
    {
        "name": "generate_report",
        "description": "Generate the natural-language report narrative from results.",
        "input_schema": {"agent_id": "string", "results": "object"},
    },
]


class MetaAgent:
    def __init__(
        self,
        agent,
        playbook: Playbook,
        config: Optional[FrameworkConfig] = None,
        backend: Optional[LLMBackend] = None,
        progress: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.agent = agent
        self.playbook = playbook
        self.config = config or CONFIG
        self.backend = backend or DEFAULT_BACKEND
        self.judge = ClaudeJudge(self.backend, model=self.config.judge_model)
        self.detector = HallucinationDetector(self.backend, model=self.config.judge_model)
        self.runner = AgentRunner(
            agent, playbook, self.config, self.judge, self.detector, progress=progress
        )
        self._progress = progress or (lambda ev, msg: None)
        self._traces: Dict[str, AgentTrace] = {}
        self.plan_rationale: str = ""

    # ── orchestration entrypoint ──────────────────────────────────────────
    def orchestrate(self, scenario_ids: Optional[List[str]] = None) -> EvaluationReport:
        self._progress("meta", "Reading playbook and planning evaluation")
        selected = scenario_ids or self.plan_evaluation()

        self._progress("meta", f"Running {len(selected)} test case(s)")
        report = self.runner.run(selected)

        # cache traces so evaluate_trace() can operate post-hoc
        for r in report.test_case_results:
            if r.trace is not None:
                self._traces[r.trace.trace_id] = r.trace

        self._progress("meta", "Synthesizing verdict and narrative report")
        self._narrate(report)
        return report

    # ── LLM planning ──────────────────────────────────────────────────────
    def plan_evaluation(self) -> List[str]:
        """Ask Claude which scenarios to run and why. Falls back to all."""
        all_ids = [s.id for s in self.playbook.test_scenarios]
        if not all_ids:
            return []
        scenario_brief = [
            {
                "id": s.id,
                "name": s.name,
                "difficulty": s.difficulty.value,
                "tags": s.tags,
            }
            for s in self.playbook.test_scenarios
        ]
        prompt = (
            f"You are planning an evaluation of agent '{self.playbook.agent_id}' "
            f"(domain: {self.playbook.domain}). Available test scenarios:\n"
            f"{json.dumps(scenario_brief, indent=2)}\n\n"
            "Select which scenarios to run for a thorough evaluation and give a "
            "one-line rationale. By default, prefer to run ALL scenarios (broad "
            "coverage including adversarial/edge cases). Return JSON: "
            '{"selected_test_case_ids": [string], "rationale": string}'
        )
        data = self.backend.complete_json(prompt, system=META_SYSTEM, model=self.config.primary_model)
        selected = data.get("selected_test_case_ids")
        self.plan_rationale = str(data.get("rationale", ""))
        if isinstance(selected, list) and selected:
            valid = [s for s in selected if s in all_ids]
            if valid:
                return valid
        # safe fallback: run everything
        self.plan_rationale = self.plan_rationale or "Fallback: running all scenarios."
        return all_ids

    # ── narrative synthesis ─────────────────────────────────────────────────
    def _narrate(self, report: EvaluationReport) -> None:
        summary = report.to_dict()["test_run_summary"]
        context = {
            "agent_id": report.agent_id,
            "summary": summary,
            "metric_scores": {k: v["score"] for k, v in report.metric_scores.items()},
            "critical_violations": report.critical_violations[:10],
            "rule_results": [
                {"rule_id": r["rule_id"], "status": r["status"], "score": r["score"]}
                for r in report.playbook_rule_results
            ],
            "plan_rationale": self.plan_rationale,
        }
        blob = json.dumps(context, indent=2)
        report.executive_summary = self.judge.generate_executive_summary(
            blob, model=self.config.primary_model
        )
        report.recommendations = self.judge.generate_recommendations(
            blob, model=self.config.primary_model
        )

    # ── tool surface (callable + dispatchable) ──────────────────────────────
    def dispatch(self, tool_name: str, args: Dict[str, Any]) -> Any:
        """Invoke one of the Meta-Agent's tools by name."""
        fn = {
            "get_playbook_rules": self.tool_get_playbook_rules,
            "run_test_case": self.tool_run_test_case,
            "evaluate_trace": self.tool_evaluate_trace,
            "check_hallucination": self.tool_check_hallucination,
            "generate_report": self.tool_generate_report,
        }.get(tool_name)
        if fn is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        return fn(**args)

    def tool_get_playbook_rules(self, agent_id: str) -> List[Dict[str, Any]]:
        return [
            {
                "id": r.id,
                "name": r.name,
                "priority": r.priority.value,
                "evaluation_type": r.evaluation_type.value,
                "weight": r.weight,
            }
            for r in self.playbook.rules
        ]

    def tool_run_test_case(self, agent_id: str, test_case_id: str, prompt: str = "") -> Dict[str, Any]:
        scenario = self.playbook.scenario_by_id(test_case_id)
        if scenario is None:
            raise ValueError(f"Unknown test_case_id: {test_case_id}")
        result = self.runner.run_test_case(scenario)
        if result.trace is not None:
            self._traces[result.trace.trace_id] = result.trace
        return result.to_dict(include_trace=True)

    def tool_evaluate_trace(self, trace_id: str, rule_id: str) -> Dict[str, Any]:
        trace = self._traces.get(trace_id)
        rule = self.playbook.rule_by_id(rule_id)
        if trace is None or rule is None:
            raise ValueError(f"Unknown trace_id={trace_id} or rule_id={rule_id}")
        return self.judge.evaluate_rule(rule, trace.final_output, trace).to_dict()

    def tool_check_hallucination(self, text: str, domain: str = "general") -> Dict[str, Any]:
        return self.detector.detect(text, domain=domain).to_dict()

    def tool_generate_report(self, agent_id: str, results: Dict[str, Any]) -> Dict[str, Any]:
        blob = json.dumps(results, indent=2)[:6000]
        return {
            "executive_summary": self.judge.generate_executive_summary(blob, model=self.config.primary_model),
            "recommendations": self.judge.generate_recommendations(blob, model=self.config.primary_model),
        }
