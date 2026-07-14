"""End-to-end demo of the Agent Testing & Evaluation Framework.

Runs the bundled flawed cybersecurity agent (``ExampleSecOpsAgent``) against its
playbook using the 10 hand-written test cases, evaluates everything through the
Claude Code CLI, then produces JSON + HTML + console reports and opens the HTML
in a browser.

    python demo.py

Prerequisite: the `claude` CLI must be installed and logged in.
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from rich.console import Console

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from agents_under_test.example_agent import ExampleSecOpsAgent  # noqa: E402
from config import CONFIG  # noqa: E402
from core.llm_client import DEFAULT_BACKEND  # noqa: E402
from core.meta_agent import MetaAgent  # noqa: E402
from core.playbook_loader import load_playbook  # noqa: E402
from reporters import ConsoleReporter, HTMLReporter, JSONReporter  # noqa: E402
from test_cases.case_generator import load_cases_json  # noqa: E402

console = Console()


def main() -> int:
    console.rule("[bold cyan]Agent Testing & Evaluation Framework — Demo")

    if not DEFAULT_BACKEND.is_available():
        console.print(
            "[bold red]The `claude` CLI is not on PATH.[/bold red] This demo uses "
            "the Claude Code CLI as its LLM engine. Install it and log in, then "
            "re-run `python demo.py`."
        )
        return 2

    console.print("[dim]› preflight: checking model availability…[/dim]")
    bad = DEFAULT_BACKEND.verify_models([CONFIG.primary_model, CONFIG.judge_model])
    if bad:
        console.print("[bold red]Your `claude` login rejected these model id(s):[/bold red]")
        for m, err in bad.items():
            console.print(f"  [red]•[/red] {m}: {str(err)[:120]}")
        console.print(
            "Set valid ids via the ATF_PRIMARY_MODEL / ATF_JUDGE_MODEL env vars "
            "(try claude-haiku-4-5, claude-sonnet-4-6, or claude-opus-4-8)."
        )
        return 3

    playbook_path = HERE / "playbooks" / "example_agent.yaml"
    cases_path = HERE / "test_cases" / "examples" / "basic_qa_cases.json"

    console.print(f"[cyan]›[/cyan] Loading playbook: {playbook_path.name}")
    playbook = load_playbook(playbook_path)

    console.print(f"[cyan]›[/cyan] Loading 10 test cases: {cases_path.name}")
    playbook.test_scenarios = load_cases_json(str(cases_path))

    agent = ExampleSecOpsAgent()
    console.print(
        f"[cyan]›[/cyan] Agent under test: [bold]{agent.agent_id}[/bold] "
        f"(deliberately flawed)\n"
        f"[cyan]›[/cyan] Judge model: {CONFIG.judge_model} · "
        f"Primary model: {CONFIG.primary_model}\n"
    )

    def progress(ev, msg):
        if ev == "case_done":
            console.print(f"  [green]✓[/green] {msg}")
        elif ev == "meta":
            console.print(f"[magenta]meta-agent:[/magenta] {msg}")

    meta = MetaAgent(agent, playbook, CONFIG, DEFAULT_BACKEND, progress=progress)
    report = meta.orchestrate()

    # Reports
    json_path = JSONReporter(CONFIG.reports_dir).write(report, filename="demo_report.json")
    html_path = HTMLReporter(CONFIG.reports_dir).write(report, filename="demo_report.html")

    console.print()
    ConsoleReporter(console).render(report)

    console.print(f"\n[green]JSON:[/green] {json_path}")
    console.print(f"[green]HTML:[/green] {html_path}")

    # LLM usage summary
    console.print(
        f"[dim]LLM calls: {len(DEFAULT_BACKEND.call_log)} · "
        f"~{DEFAULT_BACKEND.total_tokens} tokens · "
        f"{DEFAULT_BACKEND.total_latency_ms/1000:.1f}s total[/dim]"
    )

    try:
        webbrowser.open(html_path.resolve().as_uri())
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
