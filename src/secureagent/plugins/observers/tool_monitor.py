"""Optional, advisory per-turn tool-call monitor."""

import hashlib
import json
from threading import Event
from secureagent.plugins.turn_events import ToolEventOutput


def install(scope, ctx):
    plugin = ToolMonitorPlugin()
    # 绑定监控器及其轮次事件订阅，卸载时清理。
    scope.register_cleanup(plugin.close)
    scope.register_cleanup(ctx.turn_events.subscribe("tool-monitor", plugin.new_turn))


class ToolMonitorPlugin:
    def __init__(self, thresholds=(3, 5, 8)):
        self.thresholds = frozenset(thresholds)
        self._closed = Event()

    def close(self):
        self._closed.set()

    def new_turn(self):
        return TurnMonitor(self)


class TurnMonitor:
    def __init__(self, plugin):
        self.plugin = plugin
        self.count = 0
        self._last = None
        self._repeats = 0

    def observe(self, call):
        if self.plugin._closed.is_set():
            return None
        function = call.get("function") or {}
        name = function.get("name")
        if not isinstance(name, str):
            name = ""
        raw = function.get("arguments", "{}")
        try:
            args = json.loads(raw)
            canonical = json.dumps(args, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        except (TypeError, ValueError, RecursionError):
            canonical = str(raw)
        signature = hashlib.sha256((name + "\0" + canonical).encode("utf-8", "replace")).digest()
        self.count += 1
        self._repeats = self._repeats + 1 if signature == self._last else 1
        self._last = signature
        return {"count": self.count, "consecutive": self._repeats,
                "remind": self._repeats in self.plugin.thresholds}

    def tool_result(self, call, result):
        observation = self.observe(call)
        if observation is None:
            return None
        notice = ("[工具调用提醒] 检测到连续重复相同的工具调用。请先检查前一次结果；"
                  "如果任务尚未完成，请考虑调整方法或参数；如果已有足够依据，请完成回答。") if observation["remind"] else None
        return ToolEventOutput("plugin/tool-monitor", observation, notice)
