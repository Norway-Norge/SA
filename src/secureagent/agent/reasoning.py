"""Per-request delivery of provider reasoning, separate from tool arguments."""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

ReasoningSink = Callable[[str, str], None]
_sink: ContextVar[ReasoningSink | None] = ContextVar("reasoning_sink", default=None)
ToolSink = Callable[[str, dict[str, Any]], None]
_tool_sink: ContextVar[ToolSink | None] = ContextVar("tool_sink", default=None)
_test_source_sink: ContextVar[Callable[[str], None] | None] = ContextVar("test_source_sink", default=None)
_journal_sink: ContextVar[Callable[[str, dict[str, Any]], None] | None] = ContextVar("journal_sink", default=None)


@contextmanager
def journal_events(sink: Callable[[str, dict[str, Any]], None]) -> Iterator[None]:
    token = _journal_sink.set(sink)
    try:
        yield
    finally:
        _journal_sink.reset(token)


def journal_event(kind: str, data: dict[str, Any]) -> None:
    sink = _journal_sink.get()
    if sink:
        sink(kind, data)


@contextmanager
def reasoning_events(sink: ReasoningSink) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def reasoning_sink() -> ReasoningSink | None:
    return _sink.get()


@contextmanager
def tool_events(sink: ToolSink) -> Iterator[None]:
    token = _tool_sink.set(sink)
    try:
        yield
    finally:
        _tool_sink.reset(token)


def tool_sink() -> ToolSink | None:
    return _tool_sink.get()


@contextmanager
def test_source_events(sink: Callable[[str], None]) -> Iterator[None]:
    token = _test_source_sink.set(sink)
    try:
        yield
    finally:
        _test_source_sink.reset(token)


def test_source_sink() -> Callable[[str], None] | None:
    return _test_source_sink.get()
