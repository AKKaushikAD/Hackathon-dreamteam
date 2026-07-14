"""Rich terminal reporter — colored tables, trees, and panels."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from core.runner import EvaluationReport

_VERDICT_STYLE = {"PASS": "bold green", "CONDITIONAL_PASS": "bold yellow", "FAIL": "bold red"}
_STATUS_STYLE = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}
_GRADE_STYLE = {"A": "bold green", "B": "green", "C": "yellow", "D": "orange1", "F": "bold red"}


class ConsoleReporter:
    def __init__(self, console: Optional[Console] = None) -> None:
        self.console = console or Console()

    # ── progress helpers (used during a run) ──────────────────────────────
    @contextmanager
    def progress(self, total: int, description: str = "Evaluating"):
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=self.console,
            transient=True,
        ) as prog:
            task = prog.add_task(description, total=total)
            yield prog, task

    def info(self, message: str) -> None:
        self.console.print(f"[cyan]›[/cyan] {message}")

    # ── full report ────────────────────────────────────────────────────────
    def render(self, report: EvaluationReport) -> None:
        c = self.console
        data = report.to_dict()
        summary = data["test_run_summary"]

        self._header(report, summary)
        self._verdict_banner(summary)
        self._metric_table(report)
        self._rule_tree(report)
        self._critical_panel(report)
        self._case_table(report)
        self._summary_and_recs(report)

    # ── sections ─────────────────────────────────────────────────────────
    def _header(self, report: EvaluationReport, summary) -> None:
        grade = summary["grade"]
        title = Text.assemble(
            ("Agent Evaluation Report\n", "bold white"),
            (f"agent: {report.agent_id}   ", "cyan"),
            (f"playbook v{report.playbook_version}   ", "cyan"),
            (f"score: {summary['overall_score']:.1f}   ", "white"),
            (f"grade: {grade}", _GRADE_STYLE.get(grade, "white")),
        )
        self.console.print(Panel(title, border_style="cyan", title="ATF"))

    def _verdict_banner(self, summary) -> None:
        verdict = summary["verdict"]
        style = _VERDICT_STYLE.get(verdict, "bold white")
        banner = Text(f"  VERDICT: {verdict}  ", style=style)
        counts = (
            f"passed={summary['passed']}  warnings={summary['warnings']}  "
            f"failed={summary['failed']}  total={summary['total_test_cases']}"
        )
        self.console.print(Panel(Text.assemble(banner, ("\n" + counts, "dim")),
                                 border_style=style.split()[-1]))

    def _metric_table(self, report: EvaluationReport) -> None:
        table = Table(title="Metric Scores (weighted)", title_style="bold", expand=True)
        table.add_column("Metric", style="cyan")
        table.add_column("Score", justify="right")
        table.add_column("Weight", justify="right")
        table.add_column("Contribution", justify="right")
        table.add_column("Bar", ratio=1)
        for name, m in report.metric_scores.items():
            score = m["score"]
            weight = m["weight"]
            contrib = score * weight
            table.add_row(
                name,
                self._score_text(score),
                f"{weight:.2f}",
                f"{contrib:.1f}",
                self._bar(score),
            )
        self.console.print(table)

    def _rule_tree(self, report: EvaluationReport) -> None:
        tree = Tree("[bold]Playbook Rule Results[/bold]")
        for r in report.playbook_rule_results:
            style = _STATUS_STYLE.get(r["status"], "white")
            node = tree.add(
                f"[{style}]{r['status']}[/{style}] "
                f"{r['rule_id']} ({r['priority']}) — {r['rule_name']} "
                f"[dim]{r['score']:.0f}[/dim]"
            )
            for v in r["violations"][:3]:
                node.add(f"[red]• {v['description'][:120]}[/red] [dim]({v['test_case_id']})[/dim]")
        self.console.print(tree)

    def _critical_panel(self, report: EvaluationReport) -> None:
        if not report.critical_violations:
            self.console.print(Panel("No critical violations.", border_style="green",
                                     title="Critical Violations"))
            return
        body = Text()
        for v in report.critical_violations:
            body.append("● ", style="bold red")
            body.append(f"[{v.get('rule_id') or '—'} @ {v['test_case_id']}] ", style="yellow")
            body.append(f"{v['description']}\n", style="white")
        self.console.print(Panel(body, border_style="red", title="⚠ Critical Violations"))

    def _case_table(self, report: EvaluationReport) -> None:
        table = Table(title="Test Case Results", expand=True)
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Status", justify="center")
        table.add_column("Score", justify="right")
        for r in report.test_case_results:
            style = _STATUS_STYLE.get(r.status, "white")
            table.add_row(
                r.test_case_id,
                r.name[:40],
                f"[{style}]{r.status}[/{style}]",
                self._score_text(r.weighted_score),
            )
        self.console.print(table)

    def _summary_and_recs(self, report: EvaluationReport) -> None:
        if report.executive_summary:
            self.console.print(
                Panel(report.executive_summary, title="Executive Summary",
                      border_style="cyan")
            )
        if report.recommendations:
            body = Text()
            for i, rec in enumerate(report.recommendations, 1):
                body.append(f"{i}. ", style="bold cyan")
                body.append(f"{rec}\n")
            self.console.print(Panel(body, title="Recommendations", border_style="cyan"))

    # ── little formatters ────────────────────────────────────────────────
    @staticmethod
    def _score_style(score: float) -> str:
        if score >= 85:
            return "green"
        if score >= 70:
            return "yellow"
        if score >= 60:
            return "orange1"
        return "red"

    def _score_text(self, score: float) -> Text:
        return Text(f"{score:.1f}", style=self._score_style(score))

    def _bar(self, score: float, width: int = 24) -> Text:
        filled = int(round(width * score / 100.0))
        return Text("█" * filled + "·" * (width - filled), style=self._score_style(score))
