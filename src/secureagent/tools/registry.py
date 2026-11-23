"""Registry for trusted, locally registered tool capabilities."""

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    schema: dict
    handler: Callable[[dict], dict]


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._lock = RLock()

    def register(self, schema: dict, handler: Callable[[dict], dict]) -> Callable[[], None]:
        """Register a tool and return an idempotent, registration-specific disposer."""
        function = schema.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        parameters = function.get("parameters") if isinstance(function, dict) else None
        if not isinstance(name, str) or not name or not isinstance(parameters, dict):
            raise ValueError("工具定义无效")
        if not callable(handler):
            raise TypeError("工具处理函数必须可调用")
        definition = ToolDefinition(name, schema, handler)
        with self._lock:
            if name in self._tools:
                raise ValueError(f"工具已注册：{name}")
            self._tools[name] = definition

        def dispose() -> None:
            with self._lock:
                if self._tools.get(name) is definition:
                    del self._tools[name]

        return dispose

    def get(self, name: str) -> ToolDefinition | None:
        with self._lock:
            return self._tools.get(name)

    def snapshot(self) -> dict[str, ToolDefinition]:
        with self._lock:
            return dict(self._tools)

    @property
    def schemas(self) -> list[dict]:
        with self._lock:
            return [tool.schema for tool in self._tools.values()]
