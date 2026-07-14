"""CLI entry point for the Agent Testing & Evaluation Framework.

Commands:
    test              Test an agent against its playbook.
    generate-playbook Claude-synthesize a playbook from an agent metadata .txt.
    generate-cases    Claude-generate test cases from a playbook.
    compare           Regression-compare two agent versions.
    validate-playbook Validate a playbook file against the schema.
    list-metrics      List all metrics and weights.

The LLM engine is the Claude Code CLI; ensure you are logged in (`claude`).
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import webbrowser
from pathlib import Path
from typing import List, Optional

# Ensure UTF-8 console output on Windows (cp1252 can't encode ✓, em-dashes, …).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

import click
from rich.console import Console

from config import CONFIG, FrameworkConfig
from core.llm_client import DEFAULT_BACKEND
from core.meta_agent import MetaAgent
from core.playbook_loader import (
    Playbook,
    PlaybookValidationError,
    load_playbook,
    validate_playbook_file,
    validate_semantics,
)
from core.runner import AgentRunner
from reporters import ConsoleReporter, HTMLReporter, JSONReporter

console = Console()


# ── helpers ──────────────────────────────────────────────────────────────────
def _apply_model_override(model: Optional[str]) -> FrameworkConfig:
    cfg = CONFIG
    if model:
        cfg.primary_model = model
        cfg.judge_model = model
    return cfg


def _check_backend() -> None:
    if not DEFAULT_BACKEND.is_available():
        console.print(
            f"[bold red]Error:[/bold red] the '{CONFIG.claude_binary}' CLI was not "
            "found on PATH. This framework uses the Claude Code CLI as its LLM "
            "engine. Install it and run `claude` once to log in."
        )
        sys.exit(2)


def _preflight_models(cfg: FrameworkConfig) -> None:
    """Fail fast (with a clear message) if a configured model id is rejected."""
    console.print("[dim]› preflight: checking model availability…[/dim]")
    bad = DEFAULT_BACKEND.verify_models([cfg.primary_model, cfg.judge_model])
    if bad:
        console.print("[bold red]Error:[/bold red] your `claude` login rejected these model id(s):")
        for m, err in bad.items():
            console.print(f"  [red]•[/red] {m}: {str(err)[:120]}")
        console.print(
            "Set valid ids via --model or the ATF_*_MODEL env vars "
            "(e.g. claude-haiku-4-5, claude-sonnet-4-6, claude-opus-4-8)."
        )
        sys.exit(3)


def _load_agent(agent_path: Optional[str]):
    """Load an agent instance from a .py file, or default to the example agent."""
    if not agent_path:
        from agents_under_test.example_agent import ExampleSecOpsAgent

        return ExampleSecOpsAgent()

    path = Path(agent_path)
    if not path.exists():
        raise click.ClickException(f"Agent file not found: {path}")

    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    if spec is None or spec.loader is None:
        raise click.ClickException(f"Could not import agent module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    from agents_under_test.base_agent import BaseAgent

    if hasattr(module, "AGENT"):
        return getattr(module, "AGENT")
    if hasattr(module, "get_agent"):
        return module.get_agent()
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, BaseAgent) and obj is not BaseAgent:
            return obj()
    raise click.ClickException(
        f"No agent found in {path}. Provide an `AGENT` instance, a `get_agent()` "
        "factory, or a subclass of BaseAgent."
    )


def _maybe_load_cases(playbook: Playbook, cases_path: Optional[str]) -> Playbook:
    if not cases_path:
        return playbook
    from test_cases.case_generator import load_cases_json

    cases = load_cases_json(cases_path)
    if not cases:
        console.print(f"[yellow]Warning:[/yellow] no valid cases in {cases_path}")
        return playbook
    playbook.test_scenarios = cases
    console.print(f"[cyan]›[/cyan] Loaded {len(cases)} test case(s) from {cases_path}")
    return playbook


def _run(agent, playbook, cfg, use_meta: bool):
    reporter = ConsoleReporter(console)
    total = len(playbook.test_scenarios)

    def progress(ev, msg):
        if ev == "case_start":
            console.print(f"  [dim]▶ running[/dim] {msg}")
        elif ev == "case_done":
            console.print(f"  [green]✓[/green] {msg}")
        elif ev == "meta":
            console.print(f"[magenta]meta-agent:[/magenta] {msg}")

    console.print(f"[cyan]›[/cyan] Evaluating '{agent.agent_id}' over {total} test case(s) "
                  f"(judge={cfg.judge_model})")
    if use_meta:
        meta = MetaAgent(agent, playbook, cfg, DEFAULT_BACKEND, progress=progress)
        report = meta.orchestrate()
    else:
        runner = AgentRunner(agent, playbook, cfg, progress=progress)
        report = runner.run()
        # still generate narrative for parity
        from core.evaluator import ClaudeJudge
        import json as _json

        judge = ClaudeJudge(DEFAULT_BACKEND, model=cfg.judge_model)
        blob = _json.dumps(report.to_dict()["test_run_summary"])
        report.executive_summary = judge.generate_executive_summary(blob, model=cfg.primary_model)
        report.recommendations = judge.generate_recommendations(blob, model=cfg.primary_model)
    return report


# ── CLI group ──────────────────────────────────────────────────────────────
@click.group()
def cli() -> None:
    """Agent Testing & Evaluation Framework — Meta-Agent CLI."""


@cli.command()
@click.option("--agent", "agent_path", default=None, help="Path to an agent .py file (default: bundled example).")
@click.option("--playbook", "playbook_path", required=True, help="Path to the playbook YAML/JSON.")
@click.option("--cases", "cases_path", default=None, help="Optional JSON case file to use instead of the playbook's scenarios.")
@click.option("--output", "output_dir", default="reports", help="Output directory for reports.")
@click.option("--format", "formats", default="console,json,html", help="Comma list: console,json,html.")
@click.option("--model", default=None, help="Override the Claude model (e.g. claude-opus-4-8).")
@click.option("--no-meta", is_flag=True, help="Skip Claude meta-agent planning; run all cases directly.")
@click.option("--open/--no-open", "open_html", default=True, help="Open the HTML report in a browser.")
def test(agent_path, playbook_path, cases_path, output_dir, formats, model, no_meta, open_html):
    """Test a single agent against its playbook."""
    _check_backend()
    cfg = _apply_model_override(model)
    _preflight_models(cfg)
    try:
        playbook = load_playbook(playbook_path)
    except PlaybookValidationError as exc:
        raise click.ClickException(str(exc))
    for w in validate_semantics(playbook):
        console.print(f"[yellow]playbook warning:[/yellow] {w}")

    playbook = _maybe_load_cases(playbook, cases_path)
    agent = _load_agent(agent_path)

    report = _run(agent, playbook, cfg, use_meta=not no_meta)

    fmts = {f.strip().lower() for f in formats.split(",") if f.strip()}
    if "console" in fmts:
        ConsoleReporter(console).render(report)
    if "json" in fmts:
        path = JSONReporter(output_dir).write(report)
        console.print(f"[green]JSON report:[/green] {path}")
    if "html" in fmts:
        path = HTMLReporter(output_dir).write(report)
        console.print(f"[green]HTML report:[/green] {path}")
        if open_html:
            try:
                webbrowser.open(path.resolve().as_uri())
            except Exception:
                pass
    console.print(
        f"[bold]Verdict:[/bold] {report.verdict}  "
        f"[bold]Score:[/bold] {report.overall_score:.1f} ({report.grade})"
    )


@cli.command("generate-playbook")
@click.option("--meta", "meta_path", required=True, help="Path to an agent metadata .txt file.")
@click.option("--output", "output_path", default=None, help="Output playbook path (default: playbooks/generated/<agent_id>.yaml).")
@click.option("--agent-id", "agent_id", default=None, help="Force the agent_id (default: derived from metadata/filename).")
@click.option("--rules", "num_rules", default=8, help="Approximate number of rules to synthesize.")
@click.option("--scenarios", "num_scenarios", default=6, help="Approximate number of test scenarios to synthesize.")
@click.option("--model", default=None, help="Override the Claude model.")
def generate_playbook(meta_path, output_path, agent_id, num_rules, num_scenarios, model):
    """Synthesize a playbook from an agent's metadata (.txt) file."""
    _check_backend()
    cfg = _apply_model_override(model)
    from core.playbook_synthesizer import PlaybookSynthesizer, PlaybookSynthesisError

    console.print(f"[cyan]›[/cyan] Reading agent metadata from {meta_path}")
    synth = PlaybookSynthesizer(DEFAULT_BACKEND, model=cfg.primary_model)
    console.print("[magenta]synthesizer:[/magenta] asking Claude to draft a playbook…")
    try:
        playbook = synth.synthesize_from_file(
            meta_path, agent_id=agent_id, num_rules=num_rules, num_scenarios=num_scenarios
        )
    except PlaybookSynthesisError as exc:
        raise click.ClickException(str(exc))

    out = Path(output_path) if output_path else Path("playbooks/generated") / f"{playbook.agent_id}.yaml"
    written = synth.write(playbook, out)

    console.print(
        f"[green]✓[/green] Synthesized playbook for '[cyan]{playbook.agent_id}[/cyan]' "
        f"(domain: {playbook.domain}) → {written}"
    )
    console.print(
        f"  {len(playbook.rules)} rule(s), {len(playbook.critical_rules)} critical · "
        f"{len(playbook.test_scenarios)} test scenario(s)"
    )
    for w in synth.warnings:
        console.print(f"[yellow]playbook warning:[/yellow] {w}")
    console.print("[dim]Review the file, then run:[/dim] "
                  f"python main.py test --playbook {written}")


@cli.command("generate-cases")
@click.option("--playbook", "playbook_path", required=True, help="Path to the playbook.")
@click.option("--count", default=12, help="Number of cases to generate.")
@click.option("--difficulty", default="mixed", help="easy|medium|hard|adversarial|mixed.")
@click.option("--output", "output_dir", default="test_cases/generated", help="Output directory.")
@click.option("--model", default=None, help="Override the Claude model.")
def generate_cases(playbook_path, count, difficulty, output_dir, model):
    """Claude-generate diverse test cases from a playbook."""
    _check_backend()
    cfg = _apply_model_override(model)
    from test_cases.case_generator import CaseGenerator

    playbook = load_playbook(playbook_path)
    console.print(f"[cyan]›[/cyan] Generating {count} cases for '{playbook.agent_id}'…")
    gen = CaseGenerator(DEFAULT_BACKEND, model=cfg.primary_model)
    scenarios = gen.generate(playbook, count=count, difficulty=difficulty)
    if not scenarios:
        raise click.ClickException("Case generation failed (LLM returned no valid cases).")
    out = Path(output_dir) / f"{playbook.agent_id}_generated.json"
    gen.write(scenarios, str(out))
    console.print(f"[green]✓[/green] Wrote {len(scenarios)} cases to {out}")
    for s in scenarios:
        console.print(f"  • [cyan]{s.id}[/cyan] ({s.difficulty.value}) {s.name}")


@cli.command()
@click.option("--agent-a", "agent_a", required=True, help="Path to agent version A.")
@click.option("--agent-b", "agent_b", required=True, help="Path to agent version B.")
@click.option("--playbook", "playbook_path", required=True, help="Path to the playbook.")
@click.option("--output", "output_dir", default="reports/comparison", help="Output directory.")
@click.option("--model", default=None, help="Override the Claude model.")
def compare(agent_a, agent_b, playbook_path, output_dir, model):
    """Compare two agent versions against the same playbook (regression testing)."""
    _check_backend()
    cfg = _apply_model_override(model)
    playbook = load_playbook(playbook_path)

    results = {}
    for label, path in (("A", agent_a), ("B", agent_b)):
        agent = _load_agent(path)
        console.print(f"[magenta]== Version {label}: {agent.agent_id} ==[/magenta]")
        runner = AgentRunner(agent, playbook, cfg)
        report = runner.run()
        JSONReporter(output_dir).write(report, filename=f"version_{label}.json")
        results[label] = report

    from rich.table import Table

    ra, rb = results["A"], results["B"]
    table = Table(title="Regression Comparison (B − A)")
    table.add_column("Metric", style="cyan")
    table.add_column("A", justify="right")
    table.add_column("B", justify="right")
    table.add_column("Δ", justify="right")
    for name in ra.metric_scores:
        a = ra.metric_scores[name]["score"]
        b = rb.metric_scores[name]["score"]
        delta = b - a
        color = "green" if delta >= 0 else "red"
        table.add_row(name, f"{a:.1f}", f"{b:.1f}", f"[{color}]{delta:+.1f}[/{color}]")
    ov = rb.overall_score - ra.overall_score
    table.add_row("OVERALL", f"{ra.overall_score:.1f}", f"{rb.overall_score:.1f}",
                  f"[{'green' if ov >= 0 else 'red'}]{ov:+.1f}[/]")
    console.print(table)
    console.print(f"A verdict: {ra.verdict} ({ra.grade}) · B verdict: {rb.verdict} ({rb.grade})")


@cli.command("validate-playbook")
@click.option("--playbook", "playbook_path", required=True, help="Path to the playbook.")
def validate_playbook_cmd(playbook_path):
    """Validate a playbook file against the schema."""
    errors = validate_playbook_file(playbook_path)
    if errors:
        console.print(f"[bold red]✗ Invalid:[/bold red] {playbook_path}")
        for e in errors:
            console.print(f"  [red]•[/red] {e}")
        sys.exit(1)
    console.print(f"[bold green]✓ Valid:[/bold green] {playbook_path}")


@cli.command("list-metrics")
def list_metrics():
    """List all available metrics, their weights, and evaluation type."""
    from metrics import ALL_METRIC_CLASSES, METRIC_DESCRIPTIONS
    from rich.table import Table

    table = Table(title="Metrics (weights sum to 1.0)")
    table.add_column("Metric", style="cyan")
    table.add_column("Weight", justify="right")
    table.add_column("Type")
    table.add_column("Description")
    for cls in ALL_METRIC_CLASSES:
        w = CONFIG.metric_weights.get(cls.name, 0.0)
        table.add_row(cls.name, f"{w:.2f}", cls.evaluation_type, METRIC_DESCRIPTIONS.get(cls.name, ""))
    console.print(table)
    console.print(f"Sum of weights: {sum(CONFIG.metric_weights.values()):.2f}")


if __name__ == "__main__":
    cli()
