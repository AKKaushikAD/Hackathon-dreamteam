# Metrics & ROI Report — Agent Testing & Evaluation Framework

**Customer:** Internal Alter Domus teams deploying LLM agents · **Date:** 2026-07-14

> **Confidence legend:** 🟢 High (measured in prototype) · 🟡 Medium (industry
> norm / defensible estimate) · 🔴 Low (assumption to validate in pilot).

---

## 1. What we measured (prototype — real data)

These are **actual results** from running the framework against the flawed
finance-advisor agent (see `docs/PROTOTYPE.md`), not projections.

| Metric | Result | Confidence |
|---|---|---|
| Verdict on a non-compliant agent | **FAIL** (correctly) | 🟢 |
| Critical violations caught | **2** (fabricated $142.75 price; 100% hallucination rate) | 🟢 |
| Rules evaluated per run | **9** (4 PASS/WARN, 3 FAIL, per-rule scored) | 🟢 |
| Metrics scored | **8 weighted dimensions** | 🟢 |
| Wall-clock per evaluation | **~3.5 min** for 4 cases (parallelized, incl. LLM judge) | 🟢 |
| Offline regression tests | **33 passing in ~0.5s** | 🟢 |
| Playbook authoring | **From a plain-text file in one command** | 🟢 |

**Core proof:** the agent scored **74.8 (grade C)** — a number a human reviewer
could easily wave through — yet the framework flagged it **unsafe for
production** because of a critical fabrication. That gap is the product.

---

## 2. Before → After

Baseline ("before") figures are from the Problem Brief and are 🔴/🟡 estimates
to validate in pilot. "After" figures are anchored to the prototype where
possible (🟢).

| Dimension | Before (manual) | After (framework) | Confidence |
|---|---|---|---|
| Time to evaluate one agent release | 1–2 engineer-days | **~3.5 min compute + ~15 min review** | Before 🔴 / After 🟢 |
| Behavior coverage per release | < 30%, happy-path | **80%+ incl. edge & adversarial** | Before 🔴 / After 🟡 |
| Adversarial / jailbreak testing | ~0% | **Built into every playbook** | 🟢 |
| Time to detect a regression | Days–weeks (often post-incident) | **Same run** (`compare` gate) | Before 🟡 / After 🟢 |
| Audit evidence per release | None standardized | **Timestamped JSON+HTML report, per-rule verdicts** | 🟢 |
| Consistency between reviewers | Varies by person | **Deterministic rubric + calibrated judge** | 🟡 |

---

## 3. ROI model (pilot horizon: 1 year, 3 teams)

### Assumptions (all 🔴/🟡 — validate in pilot)

| # | Assumption | Value | Confidence |
|---|---|---|---|
| A1 | Agents in scope across 3 pilot teams | 12 | 🔴 |
| A2 | Releases per agent per year | 12 (monthly) | 🟡 |
| A3 | Manual QA effort per release (before) | 1.5 engineer-days | 🔴 |
| A4 | Framework effort per release (after) | 0.25 engineer-days (setup + review) | 🟡 |
| A5 | Fully-loaded engineer day-rate | €600/day | 🟡 |
| A6 | Serious compliance/quality incidents/yr avoided by pre-prod gating | 1 | 🔴 |
| A7 | Cost of one such incident (remediation + client + regulatory + reputational) | €150,000 (conservative) | 🔴 |

### A) Labor savings

```
Releases/yr        = 12 agents × 12 releases            = 144
Saved days/release = 1.5 − 0.25                          = 1.25 days
Labor saved        = 144 × 1.25 × €600                   = €108,000 / yr
```

### B) Risk-avoidance value (the headline for a regulated firm)

```
Incident value     = 1 avoided incident × €150,000       = €150,000 / yr
```
*Rationale:* the prototype already caught a fabricated financial figure and a
100% hallucination that scored an otherwise "acceptable" C. In a client-facing
fund-admin context, shipping that is exactly the incident class this avoids.

### C) Total annual benefit & ROI

```
Total benefit      = €108,000 (labor) + €150,000 (risk)  = €258,000 / yr
Cost to run        = infra ~€0 (uses existing Claude CLI) + ~0.5 FTE steward
                     ≈ €60,000 / yr (fully loaded)         = €60,000
Net benefit        = €198,000 / yr
ROI                = 198,000 / 60,000                      ≈ 3.3× (330%)
Payback period     ≈ 2.8 months
```

> **Sensitivity:** even with **zero** avoided incidents (drop B entirely), labor
> savings alone (€108k vs €60k) still yield **~1.8× ROI**. The risk-avoidance
> term is upside, not the load-bearing assumption. 🟡

---

## 4. Data sources

| Source | Used for | Confidence |
|---|---|---|
| **Prototype run** (`reports/finance-advisor_report_*.json`) | Verdict, violations, scores, runtime | 🟢 |
| **Prototype test suite** (33 tests, `pytest`) | Reliability of the framework itself | 🟢 |
| Problem Brief baseline table | Before-state estimates | 🔴 |
| Industry norms (LLM eval / QA automation) | Coverage & regression-detection deltas | 🟡 |
| Alter Domus engineer day-rate | Labor cost (placeholder — replace with actual) | 🟡 |
| Incident cost assumption | Risk-avoidance value | 🔴 |

---

## 5. How to raise confidence (pilot plan)

The 🔴 items are all measurable in a **6-week, 3-team pilot**:

1. **Instrument baseline** (weeks 1–2): time-track current manual QA on real
   agent releases → replaces A3, and the Before column.
2. **Count the portfolio** (week 1): actual agents in scope → replaces A1.
3. **Deploy + gate** (weeks 3–6): run the framework on every release; log
   violations caught pre-prod → validates A6 and the risk term.
4. **Finance sign-off** on incident-cost figure → replaces A7 with a firm number.

**Success criteria for the pilot:** ≥1 material violation caught pre-production,
≥50% reduction in measured QA time, and a governance reviewer accepting the
generated report as audit evidence.

---

## 6. One-line ROI summary

> **~3.3× ROI (≈€198k net/yr, ~3-month payback)** across 3 teams — driven by
> automated QA that runs in minutes and, as the prototype already shows, catches
> compliance-critical failures a human reviewer would score as "acceptable."
> Labor savings alone justify it (~1.8×); risk avoidance is the upside.
