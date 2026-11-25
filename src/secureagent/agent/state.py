"""State kept for one user turn."""

from typing import Any, TypedDict


class AgentState(TypedDict):
    messages: list[dict[str, Any]]
    steps: int
    calls: int
    stop_reason: str
    tool_snapshot: Any
    observers: Any
