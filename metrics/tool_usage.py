"""Tool usage metric — allowed/forbidden tools, redundancy, params, required tools.

Fully deterministic: it reads the trace's tool calls and the playbook's
``tool_constraints``.
"""

from __future__ import annotations

import json
from typing import Dict, List, Tuple

from metrics.base_metric import BaseMetric, MetricContext, MetricResult, Violation


class ToolUsageMetric(BaseMetric):
    name = "tool_usage"
    evaluation_type = "deterministic"

    def evaluate(self, ctx: MetricContext) -> MetricResult:
        tc = ctx.playbook.tool_constraints
        calls = ctx.trace.tool_calls
        violations: List[Violation] = []
        penalties = 0.0

        allowed = set(tc.allowed_tools)
        forbidden = set(tc.forbidden_tools)

        # ── forbidden / disallowed tools (severe) ──────────────────────────
        forbidden_used = []
        disallowed_used = []
        for step in calls:
            name = step.tool_name or ""
            if name in forbidden:
                forbidden_used.append(name)
                violations.append(
                    Violation(
                        description=f"Used forbidden tool '{name}'.",
                        critical=True,
                        trace_step=step.step_id,
                    )
                )
                penalties += 40.0
            elif allowed and name not in allowed:
                disallowed_used.append(name)
                violations.append(
                    Violation(
                        description=f"Used tool '{name}' not in allowed_tools.",
                        critical=False,
                        trace_step=step.step_id,
                    )
                )
                penalties += 20.0

        # ── over-budget call count ─────────────────────────────────────────
        if len(calls) > tc.max_tool_calls_per_task:
            violations.append(
                Violation(
                    description=f"Exceeded max_tool_calls_per_task "
                    f"({len(calls)} > {tc.max_tool_calls_per_task}).",
                    critical=False,
                )
            )
            penalties += 15.0

        # ── redundancy (same tool + identical params) ──────────────────────
        redundant = self._redundant_calls(calls)
        if redundant:
            violations.append(
                Violation(description=f"Redundant tool calls: {redundant}", critical=False)
            )
            penalties += min(20.0, 5.0 * len(redundant))

        # ── required tools for this task type ──────────────────────────────
        missing_required: List[str] = []
        task_type = ctx.scenario.task_type
        if task_type and task_type in tc.required_tools_for_task_types:
            used = {s.tool_name for s in calls}
            for req in tc.required_tools_for_task_types[task_type]:
                if req not in used:
                    missing_required.append(req)
                    violations.append(
                        Violation(
                            description=f"Task type '{task_type}' requires tool "
                            f"'{req}' which was not called.",
                            critical=False,
                        )
                    )
                    penalties += 20.0

        # ── parameter sanity (empty params on a tool that clearly needs them) ─
        bad_params = self._suspect_params(calls)
        if bad_params:
            violations.append(
                Violation(description=f"Suspicious/empty tool params: {bad_params}", critical=False)
            )
            penalties += min(15.0, 5.0 * len(bad_params))

        score = max(0.0, 100.0 - penalties)
        # If no tools are constrained and none used, treat as fully compliant.
        if not allowed and not forbidden and not calls and not (task_type and task_type in tc.required_tools_for_task_types):
            score = 100.0

        details = {
            "tool_calls": len(calls),
            "forbidden_used": forbidden_used,
            "disallowed_used": disallowed_used,
            "redundant_calls": redundant,
            "missing_required": missing_required,
            "suspect_params": bad_params,
            "max_allowed_calls": tc.max_tool_calls_per_task,
        }
        return self._result(
            self.name, score, self.weight, details=details,
            violations=violations, pass_floor=70.0,
        )

    @staticmethod
    def _redundant_calls(calls) -> List[str]:
        seen: Dict[Tuple[str, str], int] = {}
        redundant: List[str] = []
        for step in calls:
            key = (step.tool_name or "", _stable(step.tool_params))
            seen[key] = seen.get(key, 0) + 1
            if seen[key] == 2:
                redundant.append(f"{step.tool_name}({step.tool_params})")
        return redundant

    @staticmethod
    def _suspect_params(calls) -> List[str]:
        suspect: List[str] = []
        for step in calls:
            params = step.tool_params
            if params is None:
                continue
            if isinstance(params, dict) and not params and step.tool_name:
                # a tool called with an empty dict where a name suggests input needed
                suspect.append(step.tool_name)
        return suspect


def _stable(params) -> str:
    try:
        return json.dumps(params, sort_keys=True, default=str)
    except Exception:
        return str(params)
