"""Tests for the LLM-as-Judge evaluator and JSON extraction (offline)."""

from __future__ import annotations

from core.evaluator import ClaudeJudge
from core.hallucination_detector import HallucinationDetector
from core.llm_client import extract_json


def test_extract_json_direct():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = "Here you go:\n```json\n{\"score\": 90}\n```\nThanks"
    assert extract_json(text) == {"score": 90}


def test_extract_json_embedded():
    text = 'prose before {"score": 42, "pass": true} prose after'
    obj = extract_json(text)
    assert obj["score"] == 42 and obj["pass"] is True


def test_extract_json_none():
    assert extract_json("no json here") is None


def test_judge_rule_scoring(fake_backend, playbook):
    judge = ClaudeJudge(fake_backend)
    rule = playbook.rules[0]
    res = judge.evaluate_rule(rule, '{"summary":"ok"}', trace=None)
    assert 0 <= res.score <= 100
    assert res.score == 80
    assert res.passed is True
    assert not res.degraded


def test_judge_degrades_gracefully(playbook):
    class BrokenBackend:
        def is_available(self):
            return True

        def complete_json(self, *a, **k):
            return {"_llm_error": "boom", "_llm_latency_ms": 0.0, "_llm_model": "x"}

        def complete(self, *a, **k):
            from core.llm_client import LLMResult

            return LLMResult(text="", ok=False, latency_ms=0.0, model="x", error="boom")

    judge = ClaudeJudge(BrokenBackend())
    res = judge.evaluate_rule(playbook.rules[0], "output", None)
    assert res.degraded is True
    assert res.violations  # flagged as degraded


def test_hallucination_detector(fake_backend):
    det = HallucinationDetector(fake_backend)
    report = det.detect("CVE-2021-44228 is critical.", domain="cybersecurity")
    assert 0.0 <= report.hallucination_rate <= 1.0
    assert report.degraded is False


def test_executive_summary(fake_backend):
    judge = ClaudeJudge(fake_backend)
    summary = judge.generate_executive_summary("{}")
    assert isinstance(summary, str) and summary
    recs = judge.generate_recommendations("{}")
    assert isinstance(recs, list) and recs
