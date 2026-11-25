"""Lifecycle-owned, advisory tool-result subscriptions."""

import copy
import json
import logging
from dataclasses import dataclass
from threading import RLock

from cordis_py import Service

log = logging.getLogger("secureagent.plugins")


@dataclass(frozen=True)
class ToolEventOutput:
    event: str
    data: dict
    notice: str | None = None


class TurnEventService(Service):
    def __init__(self, ctx):
        self._lock = RLock()
        self._subscriptions = {}
        super().__init__(ctx, "turn_events")

    def subscribe(self, name, factory):
        if not isinstance(name, str) or not name or not callable(factory):
            raise ValueError("事件订阅无效")
        with self._lock:
            if name in self._subscriptions:
                raise ValueError(f"事件订阅已存在：{name}")
            entry = (object(), factory)
            self._subscriptions[name] = entry

        def dispose():
            with self._lock:
                if self._subscriptions.get(name) is entry:
                    del self._subscriptions[name]
        return dispose

    def new_turn(self):
        with self._lock:
            entries = tuple(self._subscriptions.items())
        observers = []
        for name, entry in entries:
            try:
                observers.append((name, entry, entry[1]()))
            except Exception:
                log.exception("插件轮次观察器初始化失败：%s", name)
        return TurnObservers(self, observers)

    def current(self, name, entry):
        with self._lock:
            return self._subscriptions.get(name) is entry


class TurnObservers:
    def __init__(self, service, observers):
        self.service = service
        self.observers = observers

    def tool_result(self, call, result):
        outputs = []
        for name, entry, observer in self.observers:
            if not self.service.current(name, entry):
                continue
            try:
                output = observer.tool_result(copy.deepcopy(call), copy.deepcopy(result))
                if output is not None:
                    if (not isinstance(output, ToolEventOutput) or not isinstance(output.data, dict)
                            or not isinstance(output.event, str)
                            or not output.event.startswith("plugin/")
                            or (output.notice is not None and not isinstance(output.notice, str))):
                        raise ValueError("插件事件结果无效")
                    json.dumps(output.data, ensure_ascii=True)
                    outputs.append(output)
            except Exception:
                log.exception("插件工具事件观察失败：%s", name)
        return outputs
