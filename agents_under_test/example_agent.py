"""Example agent under test — a deliberately flawed cybersecurity assistant.

This agent demonstrates the framework catching real problems. It is *mostly*
competent but ships intentional flaws so the report shows a mix of PASS/FAIL:

  * FLAW 1 — Hallucination: for CVEs it can't find, it fabricates plausible
    details and a fake "patched" version instead of admitting uncertainty.
  * FLAW 2 — Missing required field: on patch-guidance answers it omits the
    ``sources`` field the playbook requires.
  * FLAW 3 — Step-count blowout / redundancy: on patch guidance it calls the
    same tool twice with identical params.

Everything else it does well (calls the CVE tool, refuses exploit requests,
professional tone), so the framework produces a realistic, mixed verdict.

The agent is deterministic (mock tools + templates) so the demo is fast and
reproducible; the *evaluation* still runs through the real Claude CLI.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

from agents_under_test.base_agent import BaseAgent
from core.trace_capture import AgentTrace

CVE_RE = re.compile(r"CVE-\d{4}-\d{4,7}", re.IGNORECASE)

# Mock CVE knowledge base the cve_lookup tool "queries".
MOCK_CVE_DB: Dict[str, Dict[str, Any]] = {
    "CVE-2021-44228": {
        "name": "Log4Shell",
        "severity": "critical",
        "cvss": 10.0,
        "product": "Apache Log4j",
        "fixed_in": "2.17.1",
        "summary": "Remote code execution via JNDI lookups in Apache Log4j 2.",
    },
    "CVE-2014-0160": {
        "name": "Heartbleed",
        "severity": "high",
        "cvss": 7.5,
        "product": "OpenSSL",
        "fixed_in": "1.0.1g",
        "summary": "Buffer over-read in OpenSSL TLS heartbeat extension.",
    },
}


class ExampleSecOpsAgent(BaseAgent):
    agent_id = "secops-assistant"

    # ── mock tools ─────────────────────────────────────────────────────────
    def _cve_lookup(self, cve_id: str) -> Optional[Dict[str, Any]]:
        return MOCK_CVE_DB.get(cve_id.upper())

    # ── main entrypoint ──────────────────────────────────────────────────────
    def run(self, prompt: str, trace: AgentTrace, scenario: Optional[Any] = None) -> str:
        low = prompt.lower()
        trace.reasoning(f"Received request: {prompt[:200]}")

        # 1) Adversarial / exploitation request -> refuse (good behavior).
        if self._is_exploit_request(low):
            trace.reasoning(
                "Request asks for a working exploit against a system the user "
                "does not own. This violates operational-safety policy. Refusing "
                "and redirecting to defensive mitigation."
            )
            out = {
                "summary": "I cannot provide a working exploit or instructions to "
                "attack systems you do not own. That would be operationally harmful.",
                "severity": "n/a",
                "recommendation": "For defense: upgrade Log4j to 2.17.1+, set "
                "log4j2.formatMsgNoLookups=true, and monitor for JNDI lookup strings "
                "in logs.",
                "sources": ["CVE-2021-44228 advisory"],
            }
            return self._emit(trace, out)

        # 2) CVE-related request.
        cve_match = CVE_RE.search(prompt)
        if cve_match:
            cve_id = cve_match.group(0).upper()
            trace.reasoning(f"Detected {cve_id}; looking it up via the cve_lookup tool.")
            record = self._cve_lookup(cve_id)
            trace.tool_call("cve_lookup", {"cve": cve_id}, result=record or "not found")

            if record is None:
                # FLAW 1: hallucinate instead of admitting the CVE is unknown.
                trace.reasoning(
                    "Tool returned nothing, but I'll provide details anyway from "
                    "memory."  # (unsupported leap — intentional flaw)
                )
                out = {
                    "summary": f"{cve_id} is a critical remote code execution "
                    "vulnerability in the AcmeCorp Gateway appliance affecting "
                    "versions 3.0 through 3.4.",
                    "severity": "critical",
                    "recommendation": "Upgrade to version 3.4.2 where it is patched.",
                    "sources": [f"{cve_id} vendor advisory"],
                }
                return self._emit(trace, out)

            # Known CVE — is this a patch-guidance task?
            is_patch = "remediate" in low or "patch" in low or "fix" in low or (
                scenario is not None and getattr(scenario, "task_type", None) == "patch-guidance"
            )
            if is_patch:
                # FLAW 3: redundant duplicate tool call (blows the step budget).
                trace.reasoning("Double-checking the patch advisory (redundant).")
                trace.tool_call("cve_lookup", {"cve": cve_id}, result=record)
                trace.tool_call("patch_advisor", {"cve": cve_id}, result={"fixed_in": record["fixed_in"]})
                # FLAW 2: omit the required 'sources' field on patch guidance.
                out = {
                    "summary": f"{cve_id} ({record['name']}): {record['summary']}",
                    "severity": record["severity"],
                    "recommendation": f"Upgrade {record['product']} to {record['fixed_in']} "
                    "or later; apply the mitigation flag as an interim measure.",
                    # 'sources' intentionally missing
                }
                return self._emit(trace, out)

            # Standard, well-formed CVE severity answer (good behavior).
            trace.reasoning("Composing a grounded, structured answer from the tool result.")
            out = {
                "summary": f"{cve_id} ({record['name']}): {record['summary']}",
                "severity": record["severity"],
                "recommendation": f"Upgrade {record['product']} to {record['fixed_in']}+ "
                "to remediate.",
                "sources": [f"{cve_id} (CVSS {record['cvss']})"],
            }
            return self._emit(trace, out)

        # 3) Generic security question — cautious, grounded answer.
        trace.reasoning("General security question; answering conservatively.")
        out = {
            "summary": "I can help with vulnerability triage, CVE lookups, and "
            "remediation guidance. Please share a CVE id or the affected product.",
            "severity": "n/a",
            "recommendation": "Provide the CVE identifier or product/version for "
            "specific guidance.",
            "sources": [],
        }
        return self._emit(trace, out)

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _is_exploit_request(low: str) -> bool:
        wants_exploit = any(w in low for w in ("exploit", "attack", "weaponize", "payload"))
        not_owned = any(w in low for w in ("don't own", "do not own", "someone else", "a company"))
        return wants_exploit and (not_owned or "write me" in low)

    @staticmethod
    def _emit(trace: AgentTrace, obj: Dict[str, Any]) -> str:
        text = json.dumps(obj, indent=2)
        trace.output(text, token_count=max(1, len(text) // 4))
        return text
