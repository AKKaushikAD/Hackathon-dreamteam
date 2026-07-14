# Agent Testing & Evaluation Framework

A **Meta-Agent** system that tests, evaluates, and generates detailed reports on
other AI agents against structured **playbooks**. It runs an agent through a
battery of test cases, scores it across **8 weighted metrics**, flags critical
rule violations, detects hallucinations, and produces JSON / HTML / terminal
reports with a Claude-generated executive summary and recommendations.

> **LLM engine: the Claude Code CLI.** Every AI-powered component (LLM-as-Judge,
> hallucination detection, reasoning scoring, report narration, playbook
> interpretation, meta-agent orchestration) runs its inference by shelling out to
> the `claude` CLI in headless print mode — **no API key required**, it uses your
> existing logged-in `claude` session. There is a single choke point
> (`core/llm_client.LLMBackend`); swapping in the HTTP API later is a one-file change.

---
## LINK TO THE DEMO VIDEO - https://alterdomusgroup-my.sharepoint.com/:v:/g/personal/sharanya_peri_alterdomus_com/IQDGZUuO1o41TowlXPvzq4txAfGpUczdZypS8v3gCPryyu0?e=p0rgUf

## Quick start

```bash
# 1. Install (Python 3.10+)
pip install -r requirements.txt

# 2. Make sure the Claude CLI is installed and logged in
claude          # log in once if prompted, then Ctrl-C
claude -p "ping" --output-format json   # should return JSON

# 3. Run the end-to-end demo (flawed example agent, 10 test cases)
python demo.py
```

`demo.py` evaluates a deliberately-flawed cybersecurity agent, writes
`reports/demo_report.json` + `reports/demo_report.html`, prints a Rich console
report, and opens the HTML report in your browser.

---

## How it works

```
        playbook.yaml                 agent under test
             │                              │
             ▼                              ▼
      ┌──────────────┐   plans/orchestrates   ┌───────────────┐
      │  Meta-Agent  │───────────────────────▶│  Agent Runner │
      │ (Claude)     │                        │  (per case)   │
      └──────────────┘                        └───────┬───────┘
             ▲                                        │ AgentTrace
             │ synthesizes verdict + narrative        ▼
             │                              ┌────────────────────┐
             │                              │  8 Metrics engine  │
             │                              │  (LLM + deterministic) │
             │                              └─────────┬──────────┘
             │            ClaudeJudge / HallucinationDetector (Claude CLI)
             ▼                                        ▼
        JSON · HTML · Console reports  ◀──────  EvaluationReport
```

1. **Playbook** (`core/playbook_loader.py`) — Pydantic-validated spec of rules,
   tool constraints, output requirements, thresholds, and test scenarios.
2. **Runner** (`core/runner.py`) — runs the agent per scenario inside a
   `TraceCapture`, executes all metrics (test cases run in parallel), aggregates
   into an `EvaluationReport`.
3. **Meta-Agent** (`core/meta_agent.py`) — Claude-driven orchestrator: reads the
   playbook, plans which cases to run, delegates to the runner/evaluators via a
   tool surface, and synthesizes the final verdict + narrative.
4. **Reporters** (`reporters/`) — JSON (canonical schema), self-contained HTML
   (dark-teal theme, animated score ring, Chart.js radar, expandable traces),
   and Rich console output.

### The 8 metrics

| Metric | Weight | Type | What it checks |
|---|---|---|---|
| `playbook_adherence` | 0.30 | hybrid | Per-rule compliance; flags critical violations |
| `task_completion` | 0.20 | hybrid | Keyword coverage + LLM semantic completion |
| `safety_guardrails` | 0.15 | hybrid | Hallucination rate, refusal accuracy, PII, injection resistance |
| `output_quality` | 0.12 | hybrid | Schema/format, required fields, tone, length, readability |
| `reasoning_quality` | 0.10 | llm | Chain-of-thought validity, no unsupported leaps |
| `tool_usage` | 0.07 | deterministic | Allowed/forbidden tools, redundancy, params, required tools |
| `efficiency` | 0.04 | deterministic | Steps vs minimum, latency, token efficiency |
| `memory_retention` | 0.02 | llm | Context consistency across multi-turn conversations |

Weights sum to 1.0 (validated at startup). **Any critical-rule violation
auto-fails the run regardless of score** (configurable).

---

## CLI

```bash
# Test an agent against its playbook (console + json + html)
python main.py test \
  --agent agents_under_test/example_agent.py \
  --playbook playbooks/example_agent.yaml \
  --cases test_cases/examples/basic_qa_cases.json \
  --output reports/ \
  --format html,json,console \
  --model claude-opus-4-8          # optional model override

# Claude-synthesize a whole playbook from an agent metadata .txt file
python main.py generate-playbook \
  --meta playbooks/metadata_examples/support_triage_agent.txt \
  --output playbooks/generated/support-triage.yaml

# Claude-generate diverse test cases from a playbook
python main.py generate-cases \
  --playbook playbooks/example_agent.yaml \
  --count 20 --difficulty mixed \
  --output test_cases/generated/

# Regression-compare two agent versions
python main.py compare \
  --agent-a path/to/agent_v1.py \
  --agent-b path/to/agent_v2.py \
  --playbook playbooks/example_agent.yaml \
  --output reports/comparison/

# Validate a playbook
python main.py validate-playbook --playbook playbooks/example_agent.yaml

# List metrics and weights
python main.py list-metrics
```

Add `--no-meta` to skip Claude planning and run every case directly. Add
`--no-open` to skip launching the browser.

---

## Generating a playbook from agent metadata

Don't want to author a playbook by hand? Describe your agent in a plain-text
file and let Claude draft one for you:

```bash
python main.py generate-playbook --meta my_agent.txt
# → playbooks/generated/<agent_id>.yaml
```

The metadata `.txt` can be free-form prose, `key: value` lines, a system
prompt, or a spec doc — anything that describes what the agent does, which
tools it may/may not use, its rules of engagement, output format, and
performance expectations. See
[`playbooks/metadata_examples/support_triage_agent.txt`](playbooks/metadata_examples/support_triage_agent.txt)
for a worked example.

Claude synthesizes behavioral `rules` (with priorities, evaluation types, and
failure examples), `tool_constraints`, `output_requirements`,
`performance_thresholds`, and seed `test_scenarios` (including adversarial
ones). The draft is validated against the same Pydantic schema every
hand-authored playbook must satisfy, then written as clean YAML. **Review the
generated file before using it** — it's a strong first draft, not a final spec.

Options: `--agent-id` to force the id, `--rules`/`--scenarios` to tune volume,
`--model` to override the model. The pipeline lives in
[`core/playbook_synthesizer.py`](core/playbook_synthesizer.py).

---

## Writing a playbook

See [`playbooks/schema.yaml`](playbooks/schema.yaml) for the full reference and
[`playbooks/example_agent.yaml`](playbooks/example_agent.yaml) for a complete
example. Templates for common agent shapes live in
[`playbooks/templates/`](playbooks/templates/). Key sections: `rules` (with
`priority`, `evaluation_type`, `weight`), `tool_constraints`,
`output_requirements`, `performance_thresholds`, and `test_scenarios`.

## Writing an agent under test

Subclass `agents_under_test.base_agent.BaseAgent` and implement `run(prompt,
trace, scenario)`, recording steps on the trace:

```python
from agents_under_test.base_agent import BaseAgent

class MyAgent(BaseAgent):
    agent_id = "my-agent"

    def run(self, prompt, trace, scenario=None):
        trace.reasoning("deciding what to do")
        trace.tool_call("search", {"q": prompt}, result=["..."])
        answer = '{"summary": "..."}'
        trace.output(answer)
        return answer
```

The CLI loads your file via `--agent`. It uses (in order) a module-level
`AGENT` instance, a `get_agent()` factory, or the first `BaseAgent` subclass.

---

## Report schema

`reporters/json_reporter.py` emits the canonical schema consumed by the HTML
viewer:

```
report_id, generated_at, agent_id, playbook_version,
test_run_summary { total_test_cases, passed, failed, warnings,
                   overall_score, grade, verdict },
metric_scores { <metric>: { score, weight, details } },
playbook_rule_results [ { rule_id, rule_name, priority, status, score,
                          violations, claude_reasoning } ],
critical_violations [ { rule_id, test_case_id, description, trace_step } ],
test_case_results [ ... ],
executive_summary,          # Claude-generated
recommendations [ ... ],    # Claude-generated
traces [ ... ]              # full traces for failed cases
```

Open any report JSON in [`web/report_viewer.html`](web/report_viewer.html)
(drag-and-drop), or open the self-contained `*_report.html` directly.

---

## Configuration

Copy `.env.example` to `.env`. Key settings (all overridable via env):

| Variable | Default | Purpose |
|---|---|---|
| `CLAUDE_BINARY` | `claude` | Path to the Claude CLI |
| `ATF_PRIMARY_MODEL` | `claude-opus-4-8` | Orchestration / synthesis model |
| `ATF_JUDGE_MODEL` | `claude-sonnet-5` | Per-rule LLM-as-judge model |
| `ATF_FAST_MODEL` | `claude-haiku-4-5` | Cheap classification model |
| `ATF_MAX_CONCURRENT_TESTS` | `4` | Parallel test cases |
| `ATF_LLM_TIMEOUT` | `120` | Per-call CLI timeout (s) |
| `ATF_MAX_RETRIES` | `3` | Retries per LLM call |

> **Tip:** the demo makes many judge calls. For faster/cheaper runs set
> `ATF_JUDGE_MODEL=claude-haiku-4-5`.

Scoring weights, grade thresholds, and the critical-violation auto-fail policy
live in `config.py` (`FrameworkConfig`).

---

## Resilience

- LLM calls retry with exponential backoff + jitter; a failed call degrades to a
  neutral, clearly-flagged result rather than crashing the run.
- A crashing agent-under-test is caught by `TraceCapture`; the error becomes a
  critical violation and evaluation continues.
- The hallucination detector falls back to a CVE-grounding heuristic if the LLM
  is unavailable.
- Malformed playbooks produce detailed, line-level validation errors.
- Every LLM call is logged with latency and token usage.

## Tests

```bash
python -m pytest        # 19 offline tests (a FakeBackend stands in for the CLI)
```

## Project layout

```
core/        meta_agent, runner, evaluator, hallucination_detector,
             playbook_loader, trace_capture, llm_client
metrics/     base_metric + the 8 scored metrics
playbooks/   schema, example, templates/
reporters/   json, html, console
test_cases/  case_generator + examples/
agents_under_test/  base_agent, example_agent
web/         report_viewer.html (standalone)
main.py      CLI      demo.py   end-to-end demo     config.py   configuration
```
