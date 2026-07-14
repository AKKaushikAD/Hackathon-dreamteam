"""Playbook parsing, validation, and typed access.

A *playbook* is the structured spec of what an agent MUST do: behavioral rules,
tool constraints, output requirements, performance thresholds, and test
scenarios. Playbooks are authored in YAML (or JSON) and validated here against a
Pydantic schema before any test run.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


class Priority(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class EvaluationType(str, Enum):
    deterministic = "deterministic"
    llm_judge = "llm_judge"
    hybrid = "hybrid"


class OutputFormat(str, Enum):
    json = "json"
    markdown = "markdown"
    html = "html"
    plain_text = "plain_text"


class Tone(str, Enum):
    professional = "professional"
    casual = "casual"
    technical = "technical"


class Difficulty(str, Enum):
    easy = "easy"
    medium = "medium"
    hard = "hard"
    adversarial = "adversarial"


class Rule(BaseModel):
    id: str
    name: str
    description: str
    priority: Priority = Priority.medium
    evaluation_type: EvaluationType = EvaluationType.llm_judge
    check_method: str = ""
    expected_behavior: str = ""
    failure_examples: List[str] = Field(default_factory=list)
    weight: float = 0.5

    @field_validator("weight")
    @classmethod
    def _weight_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError(f"rule weight must be in [0,1], got {v}")
        return v

    @property
    def is_critical(self) -> bool:
        return self.priority == Priority.critical


class ToolConstraints(BaseModel):
    allowed_tools: List[str] = Field(default_factory=list)
    forbidden_tools: List[str] = Field(default_factory=list)
    max_tool_calls_per_task: int = 50
    required_tools_for_task_types: Dict[str, List[str]] = Field(default_factory=dict)


class OutputRequirements(BaseModel):
    format: OutputFormat = OutputFormat.plain_text
    schema_def: Optional[Dict[str, Any]] = Field(default=None, alias="schema")
    required_fields: List[str] = Field(default_factory=list)
    max_length: int = 100_000
    tone: Tone = Tone.professional

    model_config = {"populate_by_name": True}


class PerformanceThresholds(BaseModel):
    max_latency_seconds: float = 60.0
    max_steps: int = 25
    min_goal_achievement_rate: float = 0.7
    max_hallucination_rate: float = 0.1


class TestScenario(BaseModel):
    id: str
    name: str = ""
    prompt: str
    expected_output_contains: List[str] = Field(default_factory=list)
    expected_output_excludes: List[str] = Field(default_factory=list)
    difficulty: Difficulty = Difficulty.medium
    tags: List[str] = Field(default_factory=list)
    task_type: Optional[str] = None


class Playbook(BaseModel):
    agent_id: str
    version: str = "0.1.0"
    description: str = ""
    domain: str = "general"
    created_at: Optional[str] = None
    author: str = "unknown"

    rules: List[Rule] = Field(default_factory=list)
    tool_constraints: ToolConstraints = Field(default_factory=ToolConstraints)
    output_requirements: OutputRequirements = Field(default_factory=OutputRequirements)
    performance_thresholds: PerformanceThresholds = Field(
        default_factory=PerformanceThresholds
    )
    test_scenarios: List[TestScenario] = Field(default_factory=list)

    # ── accessors ───────────────────────────────────────────────────────
    @property
    def critical_rules(self) -> List[Rule]:
        return [r for r in self.rules if r.is_critical]

    def rule_by_id(self, rule_id: str) -> Optional[Rule]:
        return next((r for r in self.rules if r.id == rule_id), None)

    def scenario_by_id(self, scenario_id: str) -> Optional[TestScenario]:
        return next((s for s in self.test_scenarios if s.id == scenario_id), None)


class PlaybookValidationError(Exception):
    """Raised with a detailed, human-readable message on invalid playbooks."""


def _load_raw(path: Union[str, Path]) -> Dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise PlaybookValidationError(f"Playbook file not found: {p}")
    text = p.read_text(encoding="utf-8")
    try:
        if p.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        raise PlaybookValidationError(f"Could not parse '{p}': {exc}") from exc
    if not isinstance(data, dict):
        raise PlaybookValidationError(
            f"Playbook '{p}' must be a mapping at the top level, got {type(data).__name__}"
        )
    return data


def load_playbook(path: Union[str, Path]) -> Playbook:
    """Load and validate a playbook from a YAML/JSON file."""
    data = _load_raw(path)
    return parse_playbook(data, source=str(path))


def parse_playbook(data: Dict[str, Any], source: str = "<dict>") -> Playbook:
    """Validate an already-parsed mapping into a ``Playbook``."""
    try:
        return Playbook.model_validate(data)
    except ValidationError as exc:
        raise PlaybookValidationError(_format_errors(source, exc)) from exc


def validate_playbook_file(path: Union[str, Path]) -> List[str]:
    """Validate without raising. Returns a list of error strings ([] if valid)."""
    try:
        pb = load_playbook(path)
    except PlaybookValidationError as exc:
        return [str(exc)]
    return validate_semantics(pb)


def validate_semantics(pb: Playbook) -> List[str]:
    """Non-schema checks that catch logically-broken but structurally-valid playbooks."""
    warnings: List[str] = []
    if not pb.rules:
        warnings.append("Playbook defines no rules — adherence cannot be scored.")
    ids = [r.id for r in pb.rules]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        warnings.append(f"Duplicate rule ids: {sorted(dupes)}")
    scenario_ids = [s.id for s in pb.test_scenarios]
    sdupes = {i for i in scenario_ids if scenario_ids.count(i) > 1}
    if sdupes:
        warnings.append(f"Duplicate test scenario ids: {sorted(sdupes)}")
    if not pb.test_scenarios:
        warnings.append("Playbook defines no test scenarios.")
    return warnings


def _format_errors(source: str, exc: ValidationError) -> str:
    lines = [f"Playbook '{source}' failed validation:"]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)
