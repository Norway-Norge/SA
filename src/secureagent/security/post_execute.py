"""Post-execution event decisions and required output safety finalization."""

import json
from dataclasses import dataclass
from enum import Enum
from threading import RLock

from secureagent.security.pre_execute import freeze, run_coroutine_sync


class PostDecisionKind(str, Enum):
    ACCEPT = "accept"
    REPLACE = "replace"
    BLOCK = "block"


@dataclass(frozen=True)
class PostExecuteDecision:
    kind: PostDecisionKind
    value: dict | None = None


class PostExecutePolicies:
    def __init__(self):
        self._lock = RLock()
        self._revision = 0
        self._filter = None

    def event_changed(self):
        with self._lock:
            self._revision += 1

    def register_filter(self, handler):
        if not callable(handler):
            raise TypeError("输出安全处理器必须可调用")
        entry = (object(), handler)
        with self._lock:
            if self._filter is not None:
                raise ValueError("输出安全处理器已注册")
            self._filter = entry
            self._revision += 1
        def dispose():
            with self._lock:
                if self._filter is entry:
                    self._filter = None
                    self._revision += 1
        return dispose

    def process(self, ctx, invocation, result):
        with self._lock:
            revision, output_filter = self._revision, self._filter
        failure = {"status": "UNKNOWN", "error": "工具结果安全处理失败，执行状态可能未知；请先核对，不要自动重试。"}
        if output_filter is None:
            return failure
        try:
            # Round-trip to detach from mutable provider output and validate JSON.
            original = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
            if not isinstance(original, dict):
                raise ValueError("工具结果必须是对象")
            frozen = freeze(original)

            async def waterfall():
                async def fallback(call, value):
                    if call is not invocation or value is not frozen:
                        raise ValueError("后置策略不能替换调用或原始结果")
                    return PostExecuteDecision(PostDecisionKind.ACCEPT)
                return await ctx.waterfall("tools/post-execute", invocation, frozen, fallback=fallback)

            decision = run_coroutine_sync(waterfall)
            if not isinstance(decision, PostExecuteDecision) or not isinstance(decision.kind, PostDecisionKind):
                raise ValueError("后置策略返回无效决策")
            if decision.kind == PostDecisionKind.ACCEPT and decision.value is None:
                selected = original
            elif decision.kind == PostDecisionKind.REPLACE and isinstance(decision.value, dict):
                selected = decision.value
            elif decision.kind == PostDecisionKind.BLOCK and decision.value is None:
                selected = {"status": "UNKNOWN", "error": "工具结果被安全策略阻止；操作可能已执行，请先核对，不要自动重试。"}
            else:
                raise ValueError("后置策略决策无效")
            safe = output_filter[1](invocation, selected)
            safe = json.loads(json.dumps(safe, ensure_ascii=False, allow_nan=False))
            if not isinstance(safe, dict):
                raise ValueError("输出安全处理器返回无效结果")
            with self._lock:
                if self._revision != revision or self._filter is not output_filter:
                    return failure
            return safe
        except Exception:
            return failure
