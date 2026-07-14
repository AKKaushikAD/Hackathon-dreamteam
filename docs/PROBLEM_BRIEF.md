# Problem Statement Brief — Agent Testing & Evaluation Framework

**Team:** [your names] · **Track:** Internal AI Enablement · **Date:** 2026-07-14

---

## Customer

**Internal Alter Domus teams building and deploying LLM-based agents** — the
operations, technology, and compliance teams automating knowledge work across
fund administration, private equity, real estate, and private-debt services
(e.g. investor-report drafting, document extraction, NAV/query support,
compliance checks, client-email triage).

**Primary buyer / champion:** Engineering leads and the AI governance / risk
function who must sign off before an agent touches client-facing or
regulated workflows.

---

## Problem

Teams are shipping AI agents faster than we can verify them. In a regulated
fund-administration environment, an agent that **hallucinates a financial
figure, leaks client data, gives unhedged "advice," or silently regresses when
a prompt or model changes** is not a bug — it's a compliance and reputational
event.

Today there is **no standardized, repeatable, auditable way** to test agent
behavior before and after deployment:

- **Manual spot-checking doesn't scale** and is inconsistent between reviewers.
- **Regressions go undetected** when models/prompts are updated.
- **No audit trail** — we can't show a regulator or an internal reviewer
  *evidence* that an agent was tested against defined rules.
- **Every team reinvents QA** ad hoc, with no shared bar for "good enough."

---

## Solution (one line)

A framework that scores any internal agent against a **playbook** (its rules,
tool limits, output requirements, and test scenarios), catches safety/compliance
violations, and produces an **audit-ready report** — with the playbook itself
auto-drafted from a plain-text description of the agent.

---

## Data

| Data used | Source | Sensitivity |
|---|---|---|
| **Playbooks** — behavioral rules, tool constraints, output/format rules, performance thresholds | Authored by teams, or **auto-synthesized from an agent metadata `.txt`** | Internal, non-sensitive |
| **Agent execution traces** — reasoning steps, tool calls, outputs | Captured at test time by the framework | Test data only (no prod client data required) |
| **Test scenarios** — happy-path, edge, and adversarial (jailbreak / injection) prompts | Embedded in playbook or Claude-generated | Internal |
| **Evaluation reports** — scores across 8 metrics, per-rule verdicts, violations | Produced by the framework (JSON / HTML) | Internal, audit evidence |

**Governance-friendly by design:** the LLM engine is the **Claude Code CLI using
the team's existing logged-in session** — no new API keys, no data leaving
approved tooling. Agents are tested against **mock tools and synthetic
scenarios**, so no production client data is needed to run an evaluation.

---

## Business case

- **Cut manual QA effort** — replace inconsistent human spot-checks with an
  automated, repeatable evaluation on every release.
- **Prevent incidents** — catch hallucinations, missing disclaimers, unsafe
  advice, and data-leak patterns *before* production. A single avoided
  compliance event dwarfs the cost of the framework.
- **Enable safe iteration** — `compare` two agent versions to prove a
  model/prompt change didn't regress behavior (regression testing).
- **Produce audit evidence** — every run yields a scored, timestamped report
  with per-rule pass/fail and critical-violation flags for governance sign-off.
- **Standardize the bar** — one shared definition of "correct agent behavior"
  across all internal teams.

---

## Baseline metrics (current state)

> **Status: illustrative estimates — to be validated with 2–3 internal teams.**
> These are the numbers we would measure in a 2-week baseline study; today's
> figures are directional, not audited.

| Metric | Baseline (est.) | Assumption / source |
|---|---|---|
| Manual QA time per agent, per release | **~1–2 days** of an engineer/reviewer | Assumes hand-crafted prompts + manual output review |
| % of agent behaviors actually tested | **< 30%** | Ad-hoc testing covers happy paths; edge/adversarial cases rarely tested |
| Adversarial / jailbreak coverage | **~0%** | Not part of current manual process |
| Time to detect a behavioral regression | **Days–weeks** (often only after an incident) | No automated before/after comparison exists |
| Audit evidence per agent | **None standardized** | No machine-generated, per-rule test report today |

**Framework target (see ROI report):** minutes-per-run automated evaluation,
80%+ behavior coverage including adversarial cases, regressions caught on the
same day, and an audit-ready report on every run.

---

## Proof it works (prototype)

Running the framework against a deliberately-flawed **finance-advisor agent**
(it invents prices for unknown tickers, drops the required disclaimer, and gives
unhedged advice) — the framework **catches all three flaws**, flags the critical
violations, and returns a `FAIL` verdict with a full report. Command:

```
python main.py test --agent agents_under_test/finance_advisor_agent.py \
                    --playbook playbooks/finance_advisor.yaml
```

---

## The ask

A **6-week pilot with 2–3 internal agent teams** to (1) validate the baseline
metrics above, (2) integrate the framework into their release process, and
(3) produce the first audit-ready evaluation reports for governance review.
