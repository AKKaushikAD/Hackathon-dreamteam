"""Abstract base for all metrics and the shared evaluation context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from config import CONFIG, FrameworkConfig
from core.evaluator import ClaudeJudge
from core.hallucination_detector import HallucinationDetector
from core.playbook_loader import Playbook, TestScenario
from core.trace_capture import AgentTrace

EvalType = Literal["deterministic", "llm", "hybrid"]


@dataclass
class Violation:
    """A single flagged problem, optionally tied to a rule/step."""

    description: str
    critical: bool = False
    rule_id: Optional[str] = None
    trace_step: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "critical": self.critical,
            "rule_id": self.rule_id,
            "trace_step": self.trace_step,
        }


@dataclass
class MetricResult:
    """Outcome of running one metric against one test case."""

    name: str
    score: float                       # 0..100
    weight: float
    passed: bool = True
    details: Dict[str, Any] = field(default_factory=dict)
    violations: List[Violation] = field(default_factory=list)

    @property
    def has_critical_violation(self) -> bool:
        return any(v.critical for v in self.violations)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 2),
            "weight": round(self.weight, 4),
            "passed": self.passed,
            "details": self.details,
            "violations": [v.to_dict() for v in self.violations],
        }


@dataclass
class MetricContext:
    """Everything a metric needs to evaluate one test case."""

    trace: AgentTrace
    scenario: TestScenario
    playbook: Playbook
    judge: ClaudeJudge
    detector: HallucinationDetector
    config: FrameworkConfig = field(default_factory=lambda: CONFIG)
    # Optional multi-turn conversation transcript (for memory retention).
    conversation: Optional[str] = None
    # Cache of results other metrics may reuse (e.g. hallucination report).
    shared: Dict[str, Any] = field(default_factory=dict)

    @property
    def response(self) -> str:
        return self.trace.final_output


class BaseMetric(ABC):
    """Abstract base metric.

    Subclasses set ``name`` / ``evaluation_type`` and implement :meth:`evaluate`.
    ``weight`` is injected from ``config.metric_weights`` at construction time.
    """

    name: str = "base"
    evaluation_type: EvalType = "deterministic"

    def __init__(self, weight: Optional[float] = None) -> None:
        self.weight = (
            weight if weight is not None else CONFIG.metric_weights.get(self.name, 0.0)
        )
        self._last: Optional[MetricResult] = None

    @abstractmethod
    def evaluate(self, ctx: MetricContext) -> MetricResult:
        """Evaluate one test case and return a scored ``MetricResult``."""

    def run(self, ctx: MetricContext) -> MetricResult:
        """Wrapper that guarantees a result even if the metric raises."""
        try:
            result = self.evaluate(ctx)
        except Exception as exc:  # partial-evaluation resilience
            result = MetricResult(
                name=self.name,
                score=0.0,
                weight=self.weight,
                passed=False,
                details={"error": f"{type(exc).__name__}: {exc}"},
                violations=[Violation(f"Metric '{self.name}' crashed: {exc}")],
            )
        self._last = result
        return result

    def get_score(self) -> float:
        return self._last.score if self._last else 0.0

    # ── small helpers for subclasses ──────────────────────────────────────
    @staticmethod
    def _result(
        name: str,
        score: float,
        weight: float,
        *,
        details: Optional[Dict[str, Any]] = None,
        violations: Optional[List[Violation]] = None,
        pass_floor: float = 60.0,
    ) -> MetricResult:
        score = max(0.0, min(100.0, score))
        violations = violations or []
        passed = score >= pass_floor and not any(v.critical for v in violations)
        return MetricResult(
            name=name,
            score=score,
            weight=weight,
            passed=passed,
            details=details or {},
            violations=violations,
        )
