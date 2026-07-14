"""Structured JSON report generator.

Serializes an ``EvaluationReport`` to the framework's canonical JSON schema and
writes it to disk. The output is fully self-describing and is also the data
source consumed by the HTML report viewer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Union

from core.runner import EvaluationReport


class JSONReporter:
    def __init__(self, output_dir: Union[str, Path] = "reports") -> None:
        self.output_dir = Path(output_dir)

    def build(self, report: EvaluationReport) -> Dict[str, Any]:
        return report.to_dict()

    def render(self, report: EvaluationReport, *, indent: int = 2) -> str:
        return json.dumps(self.build(report), indent=indent, default=str)

    def write(self, report: EvaluationReport, filename: str = "") -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        name = filename or f"{report.agent_id}_{report.report_id}.json"
        path = self.output_dir / name
        path.write_text(self.render(report), encoding="utf-8")
        return path
