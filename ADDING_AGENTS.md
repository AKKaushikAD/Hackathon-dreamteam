# How to Add a New Agent for Testing

This guide walks you through adding a new agent to the testing framework, using the **finance-advisor** agent as a concrete example.

---

## Overview

Adding an agent requires **3 files**:

| # | File | Purpose |
|---|------|---------|
| 1 | `agents_under_test/<name>_agent.py` | The agent implementation |
| 2 | `playbooks/<name>.yaml` | Rules, constraints, and test scenarios |
| 3 | `tests/test_<name>_agent.py` | Unit and integration tests |

---

## Step 1: Create the Agent Class

Create a new file in `agents_under_test/`. Your agent must:

- Extend `BaseAgent`
- Set `agent_id` (must match the playbook's `agent_id`)
- Implement the `run(prompt, trace, scenario)` method

```python
# agents_under_test/finance_advisor_agent.py
from agents_under_test.base_agent import BaseAgent
from core.trace_capture import AgentTrace

class FinanceAdvisorAgent(BaseAgent):
    agent_id = "finance-advisor"  # Must match playbook agent_id

    def run(self, prompt: str, trace: AgentTrace, scenario=None) -> str:
        # 1. Record your reasoning
        trace.reasoning("Analyzing the user request...")

        # 2. Call tools and record them
        result = self._stock_lookup("AAPL")
        trace.tool_call("stock_lookup", {"ticker": "AAPL"}, result=result)

        # 3. Produce output and record it
        output = '{"summary": "AAPL is at $189.50"}'
        trace.output(output, token_count=len(output) // 4)
        return output
```

### Key trace methods:

| Method | When to use |
|--------|-------------|
| `trace.reasoning(text)` | Record a thinking/decision step |
| `trace.tool_call(name, params, result=...)` | Record a tool invocation |
| `trace.memory_read(key, value)` | Record reading from memory |
| `trace.memory_write(key, value)` | Record writing to memory |
| `trace.output(text, token_count=N)` | Record the final output |

---

## Step 2: Create the Playbook

Create a YAML file in `playbooks/`. The playbook defines what "correct behavior" looks like. See `playbooks/schema.yaml` for the full schema.

```yaml
# playbooks/finance_advisor.yaml
agent_id: finance-advisor        # Must match the agent class
version: 1.0.0
description: A finance advisor agent...
domain: finance

rules:
  - id: PB-001
    name: No fabricated market data
    description: Must not invent prices or metrics.
    priority: critical
    evaluation_type: hybrid       # deterministic | llm_judge | hybrid
    check_method: Cross-check output against tool results.
    expected_behavior: Only cites data from tool calls.
    failure_examples:
      - "Invents a stock price with no tool lookup."
    weight: 1.0

tool_constraints:
  allowed_tools: [stock_lookup, risk_calculator]
  forbidden_tools: [trade_executor]
  max_tool_calls_per_task: 5
  required_tools_for_task_types:
    stock-lookup: [stock_lookup]

output_requirements:
  format: json
  required_fields: [summary, action, risk_level, disclaimer]
  max_length: 4000
  tone: professional

performance_thresholds:
  max_latency_seconds: 20.0
  max_steps: 10
  min_goal_achievement_rate: 0.75
  max_hallucination_rate: 0.1

test_scenarios:
  - id: TS-001
    name: Basic stock lookup
    prompt: "What is the current price of AAPL?"
    expected_output_contains: ["189.50", "Apple"]
    expected_output_excludes: ["buy"]
    difficulty: easy
    tags: [stock, lookup]
    task_type: stock-lookup
```

### Rule evaluation types:

- **deterministic** — checked programmatically (field presence, tool call existence)
- **llm_judge** — Claude evaluates compliance via prompt
- **hybrid** — both deterministic checks and LLM judgment

---

## Step 3: Write Tests

Create a test file in `tests/`. The framework provides a `fake_backend` fixture (defined in `conftest.py`) that mocks the LLM so tests run offline.

```python
# tests/test_finance_agent.py
import json
from agents_under_test.finance_advisor_agent import FinanceAdvisorAgent
from core.trace_capture import TraceCapture

def test_known_ticker_returns_price():
    agent = FinanceAdvisorAgent()
    with TraceCapture(agent.agent_id, "TC") as trace:
        agent.run("What is the current price of AAPL?", trace)
    output = json.loads(trace.final_output)
    assert "189.50" in output["summary"]

def test_full_run_report_shape(fake_backend):
    # Uses the fake_backend fixture for integration tests
    runner = _runner(fake_backend)
    report = runner.run()
    assert report.to_dict()["test_run_summary"]["verdict"] in ("PASS", "FAIL")
```

---

## Step 4: Run

```bash
# Run only your new agent's tests
pytest tests/test_finance_agent.py -v

# Run the full framework evaluation (requires Claude CLI)
python main.py --playbook playbooks/finance_advisor.yaml
```

---

## Checklist

- [ ] Agent class extends `BaseAgent` and sets `agent_id`
- [ ] `run()` records reasoning, tool calls, and output on the trace
- [ ] Playbook `agent_id` matches the agent class
- [ ] Playbook has rules, tool_constraints, output_requirements, performance_thresholds, test_scenarios
- [ ] Tests cover: happy path, edge cases, intentional flaws, integration with runner
- [ ] `pytest tests/test_<name>_agent.py -v` passes

---

## Tips

- **Intentional flaws** make great demos — they show the framework catching real problems (hallucination, missing fields, compliance violations).
- **Mock tools** keep the agent deterministic and fast. Real tool integrations can be swapped in later.
- **`task_type`** in test scenarios links to `required_tools_for_task_types` — the framework checks that the agent called the right tools for each task type.
- The `FakeBackend` in `conftest.py` returns passing scores by default. Instantiate `FakeBackend(score=40, passed=False)` to simulate failures.
