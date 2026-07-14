"""Central configuration for the Agent Testing & Evaluation Framework.

The framework's SOLE LLM inference engine is the **Claude Code CLI** (`claude -p`).
Every AI-powered component (LLM-as-Judge, hallucination detection, reasoning
scoring, report generation, playbook interpretation, meta-agent orchestration)
routes through `core.llm_client.LLMBackend`, which shells out to the CLI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional at runtime
    pass


def _get(name: str, default: str) -> str:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _get_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Default scoring weights. MUST sum to 1.0 (validated in __post_init__).
DEFAULT_METRIC_WEIGHTS: Dict[str, float] = {
    "playbook_adherence": 0.30,  # highest weight
    "task_completion": 0.20,
    "safety_guardrails": 0.15,
    "output_quality": 0.12,
    "reasoning_quality": 0.10,
    "tool_usage": 0.07,
    "efficiency": 0.04,
    "memory_retention": 0.02,
}

DEFAULT_GRADE_THRESHOLDS: Dict[str, int] = {"A": 90, "B": 80, "C": 70, "D": 60, "F": 0}


@dataclass
class FrameworkConfig:
    """Runtime configuration. Instantiate with `FrameworkConfig.load()`."""

    # ── LLM engine (Claude Code CLI) ──────────────────────────────────────
    # Model IDs must be accepted by your `claude` login/provider. Override via
    # env (ATF_PRIMARY_MODEL / ATF_JUDGE_MODEL / ATF_FAST_MODEL) if these are not
    # available to your key — check `list-models` below or run
    #   claude -p ok --output-format json --model <id>
    claude_binary: str = "claude"
    primary_model: str = "claude-opus-4-8"           # orchestration / synthesis
    judge_model: str = "claude-sonnet-4-6"           # per-rule LLM-as-judge calls
    fast_model: str = "claude-haiku-4-5"             # cheap classification calls

    llm_timeout_seconds: int = 120
    max_retries: int = 3
    retry_base_delay: float = 1.0                    # exponential backoff seed
    retry_max_delay: float = 30.0

    # ── Testing settings ─────────────────────────────────────────────────
    max_concurrent_tests: int = 4
    test_timeout_seconds: int = 90
    retry_on_failure: bool = True

    # ── Scoring ──────────────────────────────────────────────────────────
    metric_weights: Dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_METRIC_WEIGHTS)
    )
    grade_thresholds: Dict[str, int] = field(
        default_factory=lambda: dict(DEFAULT_GRADE_THRESHOLDS)
    )
    # A critical rule violation => automatic FAIL regardless of numeric score.
    critical_violation_auto_fail: bool = True
    # Overall score below this => CONDITIONAL_PASS instead of PASS.
    conditional_pass_floor: float = 70.0

    # ── Paths ────────────────────────────────────────────────────────────
    reports_dir: Path = field(default_factory=lambda: Path("reports"))

    def __post_init__(self) -> None:
        total = sum(self.metric_weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"metric_weights must sum to 1.0, got {total:.4f}: {self.metric_weights}"
            )
        self.reports_dir = Path(self.reports_dir)

    @classmethod
    def load(cls) -> "FrameworkConfig":
        """Build a config from environment variables (and .env)."""
        return cls(
            claude_binary=_get("CLAUDE_BINARY", "claude"),
            primary_model=_get("ATF_PRIMARY_MODEL", "claude-opus-4-8"),
            judge_model=_get("ATF_JUDGE_MODEL", "claude-sonnet-4-6"),
            fast_model=_get("ATF_FAST_MODEL", "claude-haiku-4-5"),
            llm_timeout_seconds=_get_int("ATF_LLM_TIMEOUT", 120),
            max_retries=_get_int("ATF_MAX_RETRIES", 3),
            max_concurrent_tests=_get_int("ATF_MAX_CONCURRENT_TESTS", 4),
            test_timeout_seconds=_get_int("ATF_TEST_TIMEOUT", 90),
        )

    def grade_for_score(self, score: float) -> str:
        for grade in ("A", "B", "C", "D"):
            if score >= self.grade_thresholds.get(grade, 101):
                return grade
        return "F"


# A module-level singleton for convenience; components may also accept an
# explicit config for testability.
CONFIG = FrameworkConfig.load()
