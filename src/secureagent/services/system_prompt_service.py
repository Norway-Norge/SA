"""Lifecycle-owned contributions assembled for each model request."""

from threading import RLock

from cordis_py import Service


class SystemPromptService(Service):
    def __init__(self, ctx):
        self._lock = RLock()
        self._sections = {}
        # 将此服务注册到 Cordis 上下文，供插件访问。
        super().__init__(ctx, "systemPrompt")

    def section(self, name, text, *, order=100, required_tool=None):
        if (not isinstance(name, str) or not name.strip()
                or not (isinstance(text, str) or callable(text))
                or isinstance(order, bool) or not isinstance(order, int)
                or (required_tool is not None and (not isinstance(required_tool, str) or not required_tool))):
            raise ValueError("系统提示词片段无效")
        entry = (object(), order, text, required_tool)
        with self._lock:
            if name in self._sections:
                raise ValueError(f"系统提示词片段已注册：{name}")
            self._sections[name] = entry

        def dispose():
            with self._lock:
                if self._sections.get(name) is entry:
                    del self._sections[name]
        return dispose

    def assemble(self, tool_names):
        visible = frozenset(tool_names)
        with self._lock:
            sections = tuple(self._sections.items())
        if not any(name == "base" for name, _ in sections):
            raise RuntimeError("系统基础提示词不可用")
        result = []
        for name, (_, order, text, required_tool) in sorted(sections, key=lambda item: (item[0] != "base", item[1][1], item[0])):
            if required_tool is not None and required_tool not in visible:
                continue
            value = text(visible) if callable(text) else text
            if not isinstance(value, str):
                raise TypeError(f"系统提示词片段返回值无效：{name}")
            if name == "base" and not value.startswith("你是安全任务助手。"):
                raise ValueError("系统基础提示词无效")
            if value.strip():
                result.append(value.strip())
        return "\n\n".join(result)
