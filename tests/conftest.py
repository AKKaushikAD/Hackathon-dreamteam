"""Shared pytest fixtures.

Provides a ``FakeBackend`` that mimics ``LLMBackend`` without invoking the
Claude CLI, so the test suite runs offline and deterministically.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

# Make the project root importable when running `pytest` from anywhere.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.llm_client import LLMResult  # noqa: E402


class FakeBackend:
    """Drop-in stand-in for LLMBackend used in tests (no subprocess)."""

    def __init__(self, score: int = 80, passed: bool = True) -> None:
        self.score = score
        self.passed = passed
        self.call_log = []

    def is_available(self) -> bool:
        return True

    def complete(self, prompt, *, system=None, model=None, timeout=None) -> LLMResult:
        return LLMResult(text="A concise professional summary paragraph.", ok=True,
                         latency_ms=1.0, model=model or "fake")

    def complete_json(self, prompt, *, system=None, model=None, timeout=None,
                      default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        import re

        # Batched rule evaluation: echo one result per rule id found in the prompt.
        rule_ids = re.findall(r"^- (\S+) \| priority=", prompt, flags=re.MULTILINE)
        results = [
            {
                "rule_id": rid,
                "score": self.score,
                "pass": self.passed,
                "confidence": 0.85,
                "reasoning": "fake reasoning",
                "violations": [],
                "recommendations": ["improve X"],
            }
            for rid in rule_ids
        ]
        return {
            "results": results,
            "score": self.score,
            "pass": self.passed,
            "confidence": 0.85,
            "reasoning": "fake reasoning",
            "violations": [],
            "recommendations": ["improve X"],
            # hallucination detector fields
            "flags": [],
            "total_checkable_claims": 1,
            "overall_confidence": 0.8,
            "notes": "",
            # case generator / narration fields
            "test_cases": [],
            "selected_test_case_ids": [],
            "rationale": "run all",
            "_llm_latency_ms": 1.0,
            "_llm_model": model or "fake",
        }

    @property
    def total_latency_ms(self) -> float:
        return 0.0

    @property
    def total_tokens(self) -> int:
        return 0


@pytest.fixture
def fake_backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def playbook():
    from core.playbook_loader import load_playbook

    return load_playbook(ROOT / "playbooks" / "example_agent.yaml")
