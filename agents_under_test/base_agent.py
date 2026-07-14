"""Abstract base class for agents under test.

An agent-under-test implements :meth:`run`, doing its work and recording each
reasoning / tool / memory / output step onto the provided ``trace`` (an
``AgentTrace`` with recorder methods bound by ``TraceCapture``). The runner wraps
each call in a ``TraceCapture`` context, so the agent never manages timing itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from core.trace_capture import AgentTrace


class BaseAgent(ABC):
    """Base class every agent-under-test must extend."""

    #: Stable identifier; should match the playbook's ``agent_id``.
    agent_id: str = "unnamed-agent"

    @abstractmethod
    def run(self, prompt: str, trace: AgentTrace, scenario: Optional[Any] = None) -> str:
        """Execute one task.

        Record steps via ``trace.reasoning(...)``, ``trace.tool_call(...)``,
        ``trace.memory_read/write(...)`` and finish with ``trace.output(...)``.
        Return the final output string (the runner also captures it from the
        trace, so returning it is a convenience).
        """

    def run_conversation(self, turns, trace: AgentTrace) -> str:
        """Optional multi-turn hook. Default: run each turn sequentially.

        ``turns`` is a list of user message strings. Returns the final output.
        Subclasses that maintain per-conversation memory should override this.
        """
        last = ""
        for i, turn in enumerate(turns):
            trace.reasoning(f"[turn {i + 1}] user said: {turn}")
            last = self.run(turn, trace)
        return last
