"""Full agent execution trace capture.

Defines the ``AgentTrace`` / ``TraceStep`` data model and a ``TraceCapture``
context manager that wraps any agent call, timing it and collecting the
step-by-step record (reasoning, tool calls, tool results, memory ops, output).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

StepType = Literal[
    "reasoning",
    "tool_call",
    "tool_result",
    "memory_read",
    "memory_write",
    "output",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@dataclass
class TraceStep:
    step_id: int
    timestamp: datetime
    step_type: StepType
    content: str
    tool_name: Optional[str] = None
    tool_params: Optional[Dict[str, Any]] = None
    tool_result: Optional[Any] = None
    latency_ms: float = 0.0
    token_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = _iso(self.timestamp)
        return d


@dataclass
class AgentTrace:
    trace_id: str
    agent_id: str
    test_case_id: str
    start_time: datetime
    end_time: Optional[datetime] = None
    total_latency_ms: float = 0.0
    steps: List[TraceStep] = field(default_factory=list)
    final_output: str = ""
    total_tokens_used: int = 0
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ── convenience accessors used across metrics ──────────────────────────
    @property
    def tool_calls(self) -> List[TraceStep]:
        return [s for s in self.steps if s.step_type == "tool_call"]

    @property
    def reasoning_steps(self) -> List[TraceStep]:
        return [s for s in self.steps if s.step_type == "reasoning"]

    @property
    def memory_reads(self) -> List[TraceStep]:
        return [s for s in self.steps if s.step_type == "memory_read"]

    @property
    def memory_writes(self) -> List[TraceStep]:
        return [s for s in self.steps if s.step_type == "memory_write"]

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def reasoning_text(self) -> str:
        return "\n".join(s.content for s in self.reasoning_steps)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "agent_id": self.agent_id,
            "test_case_id": self.test_case_id,
            "start_time": _iso(self.start_time),
            "end_time": _iso(self.end_time) if self.end_time else None,
            "total_latency_ms": round(self.total_latency_ms, 2),
            "steps": [s.to_dict() for s in self.steps],
            "final_output": self.final_output,
            "total_tokens_used": self.total_tokens_used,
            "error": self.error,
            "metadata": self.metadata,
        }


class TraceCapture:
    """Context manager that records an agent's full execution trace.

    Usage::

        with TraceCapture(agent_id, test_case_id) as trace:
            trace.reasoning("Deciding which tool to use")
            trace.tool_call("search", {"q": "cve-2024"}, result=[...])
            trace.output("The answer is ...")

    On exit it stamps ``end_time`` and ``total_latency_ms``. Any exception
    raised inside the block is captured into ``trace.error`` (and re-raised only
    if ``reraise=True``) so a crashing agent still produces a usable trace.
    """

    def __init__(
        self,
        agent_id: str,
        test_case_id: str,
        *,
        reraise: bool = False,
    ) -> None:
        self.trace = AgentTrace(
            trace_id=f"trace_{uuid.uuid4().hex[:12]}",
            agent_id=agent_id,
            test_case_id=test_case_id,
            start_time=_now(),
        )
        self._reraise = reraise
        self._t0 = 0.0
        self._step_t0 = 0.0

    # ── lifecycle ──────────────────────────────────────────────────────────
    def __enter__(self) -> AgentTrace:
        self._t0 = time.perf_counter()
        self._step_t0 = self._t0
        # Bind recorder methods onto the trace object for ergonomic use.
        self.trace.reasoning = self.reasoning          # type: ignore[attr-defined]
        self.trace.tool_call = self.tool_call          # type: ignore[attr-defined]
        self.trace.tool_result = self.tool_result      # type: ignore[attr-defined]
        self.trace.memory_read = self.memory_read      # type: ignore[attr-defined]
        self.trace.memory_write = self.memory_write    # type: ignore[attr-defined]
        self.trace.output = self.output                # type: ignore[attr-defined]
        return self.trace

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.trace.end_time = _now()
        self.trace.total_latency_ms = (time.perf_counter() - self._t0) * 1000.0
        if exc is not None:
            self.trace.error = f"{exc_type.__name__ if exc_type else 'Error'}: {exc}"
        return not self._reraise and exc is not None

    # ── step recorders ──────────────────────────────────────────────────────
    def _next_id(self) -> int:
        return len(self.trace.steps) + 1

    def _lap_ms(self) -> float:
        now = time.perf_counter()
        dt = (now - self._step_t0) * 1000.0
        self._step_t0 = now
        return dt

    def _add(
        self,
        step_type: StepType,
        content: str,
        *,
        tool_name: Optional[str] = None,
        tool_params: Optional[Dict[str, Any]] = None,
        tool_result: Optional[Any] = None,
        token_count: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        latency_ms: Optional[float] = None,
    ) -> TraceStep:
        step = TraceStep(
            step_id=self._next_id(),
            timestamp=_now(),
            step_type=step_type,
            content=content,
            tool_name=tool_name,
            tool_params=tool_params,
            tool_result=tool_result,
            latency_ms=latency_ms if latency_ms is not None else self._lap_ms(),
            token_count=token_count,
            metadata=metadata or {},
        )
        self.trace.steps.append(step)
        self.trace.total_tokens_used += token_count
        return step

    def reasoning(self, content: str, *, token_count: int = 0, **meta: Any) -> TraceStep:
        return self._add("reasoning", content, token_count=token_count, metadata=meta)

    def tool_call(
        self,
        tool_name: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        result: Any = None,
        latency_ms: Optional[float] = None,
        **meta: Any,
    ) -> TraceStep:
        step = self._add(
            "tool_call",
            f"call {tool_name}",
            tool_name=tool_name,
            tool_params=params or {},
            tool_result=result,
            latency_ms=latency_ms,
            metadata=meta,
        )
        if result is not None:
            self._add(
                "tool_result",
                f"result of {tool_name}",
                tool_name=tool_name,
                tool_result=result,
            )
        return step

    def tool_result(self, tool_name: str, result: Any, **meta: Any) -> TraceStep:
        return self._add(
            "tool_result", f"result of {tool_name}", tool_name=tool_name,
            tool_result=result, metadata=meta,
        )

    def memory_read(self, key: str, value: Any = None, **meta: Any) -> TraceStep:
        return self._add(
            "memory_read", f"read {key}", tool_result=value, metadata={"key": key, **meta}
        )

    def memory_write(self, key: str, value: Any = None, **meta: Any) -> TraceStep:
        return self._add(
            "memory_write", f"write {key}", tool_result=value, metadata={"key": key, **meta}
        )

    def output(self, content: str, *, token_count: int = 0, **meta: Any) -> TraceStep:
        self.trace.final_output = content
        return self._add("output", content, token_count=token_count, metadata=meta)


def trace_from_dict(data: Dict[str, Any]) -> AgentTrace:
    """Reconstruct an ``AgentTrace`` from a serialized dict (for reports/tests)."""

    def parse_dt(v: Optional[str]) -> Optional[datetime]:
        return datetime.fromisoformat(v) if v else None

    steps = [
        TraceStep(
            step_id=s["step_id"],
            timestamp=parse_dt(s["timestamp"]) or _now(),
            step_type=s["step_type"],
            content=s.get("content", ""),
            tool_name=s.get("tool_name"),
            tool_params=s.get("tool_params"),
            tool_result=s.get("tool_result"),
            latency_ms=s.get("latency_ms", 0.0),
            token_count=s.get("token_count", 0),
            metadata=s.get("metadata", {}),
        )
        for s in data.get("steps", [])
    ]
    return AgentTrace(
        trace_id=data["trace_id"],
        agent_id=data["agent_id"],
        test_case_id=data["test_case_id"],
        start_time=parse_dt(data["start_time"]) or _now(),
        end_time=parse_dt(data.get("end_time")),
        total_latency_ms=data.get("total_latency_ms", 0.0),
        steps=steps,
        final_output=data.get("final_output", ""),
        total_tokens_used=data.get("total_tokens_used", 0),
        error=data.get("error"),
        metadata=data.get("metadata", {}),
    )
