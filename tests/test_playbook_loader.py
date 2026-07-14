"""Tests for playbook parsing and validation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from core.playbook_loader import (
    PlaybookValidationError,
    load_playbook,
    parse_playbook,
    validate_semantics,
)


def test_loads_example_playbook(playbook):
    assert playbook.agent_id == "secops-assistant"
    assert playbook.domain == "cybersecurity"
    assert len(playbook.rules) >= 8
    assert playbook.critical_rules, "expected at least one critical rule"
    assert playbook.output_requirements.format.value == "json"


def test_rule_accessors(playbook):
    r = playbook.rule_by_id("PB-001")
    assert r is not None
    assert r.is_critical
    assert 0.0 <= r.weight <= 1.0


def test_invalid_weight_rejected():
    data = {
        "agent_id": "x",
        "rules": [{"id": "R1", "name": "n", "description": "d", "weight": 5.0}],
    }
    with pytest.raises(PlaybookValidationError):
        parse_playbook(data)


def test_semantic_warnings_on_empty():
    pb = parse_playbook({"agent_id": "empty"})
    warnings = validate_semantics(pb)
    assert any("no rules" in w for w in warnings)
    assert any("no test scenarios" in w for w in warnings)


def test_duplicate_rule_ids_flagged():
    data = {
        "agent_id": "dup",
        "rules": [
            {"id": "R1", "name": "a", "description": "d"},
            {"id": "R1", "name": "b", "description": "d"},
        ],
    }
    pb = parse_playbook(data)
    warnings = validate_semantics(pb)
    assert any("Duplicate rule ids" in w for w in warnings)


def test_missing_file():
    with pytest.raises(PlaybookValidationError):
        load_playbook("does/not/exist.yaml")
