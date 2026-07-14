"""Core evaluation engine for the Agent Testing & Evaluation Framework."""

from core.evaluator import ClaudeJudge, JudgeResult
from core.hallucination_detector import HallucinationDetector, HallucinationReport
from core.llm_client import DEFAULT_BACKEND, LLMBackend, LLMResult
from core.meta_agent import MetaAgent
from core.playbook_loader import (
    Playbook,
    PlaybookValidationError,
    load_playbook,
    validate_playbook_file,
)
from core.runner import AgentRunner, EvaluationReport, TestCaseResult
from core.trace_capture import AgentTrace, TraceCapture, TraceStep

__all__ = [
    "ClaudeJudge",
    "JudgeResult",
    "HallucinationDetector",
    "HallucinationReport",
    "LLMBackend",
    "LLMResult",
    "DEFAULT_BACKEND",
    "MetaAgent",
    "Playbook",
    "PlaybookValidationError",
    "load_playbook",
    "validate_playbook_file",
    "AgentRunner",
    "EvaluationReport",
    "TestCaseResult",
    "AgentTrace",
    "TraceCapture",
    "TraceStep",
]
