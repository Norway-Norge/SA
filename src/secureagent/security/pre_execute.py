"""Synchronous pre-execution policy chain for trusted in-process plugins."""

from dataclasses import dataclass
import asyncio
from contextvars import copy_context
from enum import Enum
from threading import RLock, Thread
from types import MappingProxyType
from typing import Mapping


def run_coroutine_sync(factory):
    """Run an async Cordis waterfall from synchronous host entry points."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    context = copy_context()
    values, failures = [], []
    def worker():
        try:
            values.append(context.run(lambda: asyncio.run(factory())))
        except BaseException as exc:
            failures.append(exc)
    thread = Thread(target=worker)
    thread.start()
    thread.join()
    if failures:
        raise failures[0]
    return values[0]


class DecisionKind(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"
    CANCEL = "cancel"


@dataclass(frozen=True)
class PreExecuteDecision:
    kind: DecisionKind
    reason: str = ""

    def __post_init__(self):
        if not isinstance(self.kind, DecisionKind) or not isinstance(self.reason, str) or len(self.reason) > 1000:
            raise ValueError("前置策略决策无效")


@dataclass(frozen=True)
class ToolInvocation:
    name: str
    arguments: Mapping
    tool_call_id: str
    session_id: str | None
    workspace: str
    permission_mode: str


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


class PreExecutePolicies:
    """Registered hooks compose as a waterfall; a later hard policy still applies."""

    def __init__(self):
        self._lock = RLock()
        self._hooks = []
        self._approval = None
        self._revision = 0

    def register(self, hook):
        if not callable(hook):
            raise TypeError("前置策略必须可调用")
        entry = (object(), hook)
        with self._lock:
            self._hooks.append(entry)
            self._revision += 1

        def dispose():
            with self._lock:
                if entry in self._hooks:
                    self._hooks.remove(entry)
                    self._revision += 1
        return dispose

    def register_approval(self, handler):
        """Install a trusted one-shot approval handler; absence fails closed."""
        if not callable(handler):
            raise TypeError("审批处理器必须可调用")
        entry = (object(), handler)
        with self._lock:
            if self._approval is not None:
                raise ValueError("前置策略审批处理器已注册")
            self._approval = entry
            self._revision += 1

        def dispose():
            with self._lock:
                if self._approval is entry:
                    self._approval = None
                    self._revision += 1
        return dispose

    def event_changed(self):
        with self._lock:
            self._revision += 1

    def evaluate(self, invocation, ctx, *, required_approval=None):
        with self._lock:
            hooks, approval, revision = tuple(self._hooks), self._approval, self._revision

        def dispatch(index):
            if index == len(hooks):
                return PreExecuteDecision(DecisionKind.ALLOW)
            called = False

            def next_hook():
                nonlocal called
                if called:
                    raise ValueError("前置策略不能重复调用 next")
                called = True
                return dispatch(index + 1)

            decision = hooks[index][1](invocation, next_hook)
            if not isinstance(decision, PreExecuteDecision):
                raise ValueError("前置策略返回无效决策")
            return decision

        async def waterfall():
            async def fallback(current):
                if current is not invocation:
                    raise ValueError("前置策略不能替换调用参数")
                return dispatch(0)
            return await ctx.waterfall("tools/pre-execute", invocation, fallback=fallback)

        try:
            decision = run_coroutine_sync(waterfall)
            if not isinstance(decision, PreExecuteDecision):
                raise ValueError("前置策略返回无效决策")
            if decision.kind == DecisionKind.ALLOW and required_approval is not None:
                decision = PreExecuteDecision(DecisionKind.ASK, required_approval)
            elif decision.kind == DecisionKind.ASK and required_approval is not None:
                decision = PreExecuteDecision(DecisionKind.ASK, required_approval + "；" + decision.reason)
            if decision.kind == DecisionKind.ASK:
                if approval is None or invocation.session_id is None:
                    return PreExecuteDecision(DecisionKind.DENY, "通用审批不可用，工具调用已拒绝")
                # Only an explicit True from the trusted handler grants this call once.
                if approval[1](invocation, decision.reason) is not True:
                    return PreExecuteDecision(DecisionKind.DENY, "工具调用未获批准")
                decision = PreExecuteDecision(DecisionKind.ALLOW)
            with self._lock:
                if revision != self._revision:
                    return PreExecuteDecision(DecisionKind.DENY, "前置策略已变化，请重新发起调用")
            return decision
        except Exception:
            return PreExecuteDecision(DecisionKind.DENY, "前置策略检查失败，工具调用已拒绝")
