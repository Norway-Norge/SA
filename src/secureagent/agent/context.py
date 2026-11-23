"""Project persisted session records into independent model messages."""

from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class MessageSource:
    """Zero-based position in the append-only session records."""

    record_index: int
    message_index: int


@dataclass(frozen=True)
class ContextProjection:
    """Model messages and their corresponding persisted source positions."""

    messages: list[dict[str, Any]]
    sources: tuple[MessageSource, ...]


def project_context(records: Iterable[dict[str, Any]]) -> ContextProjection:
    """Build an isolated, ordered view without changing the original records.

    Version 1 records contain the model-visible message delta for each turn.
    UI data and other record metadata are deliberately excluded. No messages
    are compacted or filtered at this stage.
    """
    messages: list[dict[str, Any]] = []
    sources: list[MessageSource] = []
    for record_index, record in enumerate(records):
        for message_index, message in enumerate(record["agent_messages"]):
            messages.append(deepcopy(message))
            sources.append(MessageSource(record_index, message_index))
    return ContextProjection(messages, tuple(sources))


_context_handler: ContextVar[Any] = ContextVar("sa_context_handler", default=None)


@contextmanager
def model_context(handler: Any):
    token = _context_handler.set(handler)
    try:
        yield
    finally:
        _context_handler.reset(token)


def current_context_handler() -> Any:
    return _context_handler.get()


class ContextCompactionError(Exception):
    pass


def is_context_overflow(exc: Exception) -> bool:
    """Only treat an explicit provider error code as context exhaustion."""
    code = getattr(exc, "code", None)
    body = getattr(exc, "body", None)
    if not code and isinstance(body, dict):
        error = body.get("error", body)
        code = error.get("code") if isinstance(error, dict) else None
    return code in {"context_length_exceeded", "context_window_exceeded"}


class ContextHandler:
    """Replace saved history for model calls without changing turn state."""

    def __init__(self, store: Any, session_id: str, history: list[dict[str, Any]], summarize: Callable):
        self.store = store
        self.session_id = session_id
        self.initial_history = deepcopy(history)
        self.history = deepcopy(history)
        self.summarize = summarize

    def messages_for_model(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self.initial_history:
            return deepcopy(messages)
        history = deepcopy(self.history)
        if history and messages and history[0].get("role") == "system" and messages[0].get("role") == "system":
            history[0] = deepcopy(messages[0])
        return history + deepcopy(messages[len(self.initial_history):])

    def compact(self) -> None:
        projection = self.store.load_context_projection(self.session_id)
        if projection.messages != self.history:
            raise ContextCompactionError("会话历史已变化，无法安全压缩；请重新发送请求。")
        if not projection.messages or projection.messages[0].get("role") != "system":
            raise ContextCompactionError("会话缺少系统消息，无法安全压缩。")
        positions = [i for i, (message, source) in enumerate(zip(projection.messages, projection.sources))
                     if message.get("role") == "user" and source.message_index >= 0]
        if len(positions) < 3:
            raise ContextCompactionError("没有足够的较早历史可压缩，请缩短本次输入或新建会话。")
        boundary = positions[-2]
        selected = projection.messages[1:boundary]
        if not selected or not _balanced(selected):
            raise ContextCompactionError("历史包含未配对的工具调用，无法安全压缩。")
        raw = self.store.load_raw_projection(self.session_id)
        boundary_source = projection.sources[boundary]
        try:
            through = raw.sources.index(boundary_source)
        except ValueError as exc:
            raise ContextCompactionError("会话历史来源无效，无法压缩。") from exc
        summary = self.summarize(deepcopy(selected))
        if not isinstance(summary, str) or not summary.strip():
            raise ContextCompactionError("摘要为空，原始上下文保持不变。")
        summary = summary.strip()
        if len(summary) >= len(str(selected)):
            raise ContextCompactionError("摘要未缩短历史，原始上下文保持不变。")
        self.store.append_context_checkpoint(self.session_id, raw, through, summary, projection.messages)
        self.history = self.store.load_context_projection(self.session_id).messages


def _balanced(messages: list[dict[str, Any]]) -> bool:
    pending: set[str] = set()
    for message in messages:
        for call in message.get("tool_calls", []):
            identifier = call.get("id")
            if not isinstance(identifier, str) or not identifier or identifier in pending:
                return False
            pending.add(identifier)
        if message.get("role") == "tool":
            identifier = message.get("tool_call_id")
            if identifier not in pending:
                return False
            pending.remove(identifier)
    return not pending
