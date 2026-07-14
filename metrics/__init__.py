"""Metrics engine — the 8 scored dimensions of agent quality.

``ALL_METRIC_CLASSES`` is the canonical registry consumed by the runner and the
``list-metrics`` CLI command.
"""

from __future__ import annotations

from typing import Dict, List, Type

from metrics.base_metric import (
    BaseMetric,
    MetricContext,
    MetricResult,
    Violation,
)
from metrics.efficiency import EfficiencyMetric
from metrics.memory_retention import MemoryRetentionMetric
from metrics.output_quality import OutputQualityMetric
from metrics.playbook_adherence import PlaybookAdherenceMetric
from metrics.reasoning_quality import ReasoningQualityMetric
from metrics.safety_guardrails import SafetyGuardrailsMetric
from metrics.task_completion import TaskCompletionMetric
from metrics.tool_usage import ToolUsageMetric

ALL_METRIC_CLASSES: List[Type[BaseMetric]] = [
    PlaybookAdherenceMetric,
    TaskCompletionMetric,
    SafetyGuardrailsMetric,
    OutputQualityMetric,
    ReasoningQualityMetric,
    ToolUsageMetric,
    EfficiencyMetric,
    MemoryRetentionMetric,
]

METRIC_DESCRIPTIONS: Dict[str, str] = {
    "playbook_adherence": "Per-rule compliance with the playbook; flags critical violations.",
    "task_completion": "Goal achievement — deterministic keyword coverage + LLM semantic judgment.",
    "safety_guardrails": "Hallucination rate, refusal accuracy, PII leakage, injection resistance.",
    "output_quality": "Schema/format compliance, required fields, tone, length, readability.",
    "reasoning_quality": "Chain-of-thought validity — coherence, no unsupported leaps.",
    "tool_usage": "Allowed/forbidden tools, redundancy, params, required tools per task type.",
    "efficiency": "Step count vs minimum, latency, and token efficiency.",
    "memory_retention": "Context consistency across multi-turn conversations.",
}


def build_metrics(config=None) -> List[BaseMetric]:
    """Instantiate all metrics with weights from config."""
    from config import CONFIG

    cfg = config or CONFIG
    return [cls(weight=cfg.metric_weights.get(cls.name, 0.0)) for cls in ALL_METRIC_CLASSES]


__all__ = [
    "BaseMetric",
    "MetricContext",
    "MetricResult",
    "Violation",
    "ALL_METRIC_CLASSES",
    "METRIC_DESCRIPTIONS",
    "build_metrics",
    "PlaybookAdherenceMetric",
    "TaskCompletionMetric",
    "SafetyGuardrailsMetric",
    "OutputQualityMetric",
    "ReasoningQualityMetric",
    "ToolUsageMetric",
    "EfficiencyMetric",
    "MemoryRetentionMetric",
]
