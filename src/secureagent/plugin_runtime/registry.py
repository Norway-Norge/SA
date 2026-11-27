"""Explicit registration of trusted in-process plugins as a Cordis service."""

from threading import RLock

from cordis_py import Service


class PluginService(Service):
    def __init__(self, runtime):
        self.runtime = runtime
        self._lock = RLock()
        self._plugins = {}
        # 将此服务注册到 Cordis 上下文，供插件访问。
        super().__init__(runtime.context, "plugins")

    def register(self, name, install, *, dependencies=()):
        """Compatibility entry point for existing trusted plugins."""
        return self._register(name, install, dependencies=dependencies, category="tool")

    def register_tool(self, name, install, *, dependencies=()):
        """Install a trusted tool or tool-observer plugin."""
        return self._register(name, install, dependencies=dependencies, category="tool")

    def register_prompt(self, name, install, *, dependencies=()):
        """Install a trusted prompt plugin."""
        return self._register(name, install, dependencies=dependencies, category="prompt")

    def _register(self, name, install, *, dependencies, category):
        if not isinstance(name, str) or not name.strip() or not callable(install):
            raise ValueError("插件名称或安装函数无效")
        with self._lock:
            if name in self._plugins:
                raise ValueError(f"插件已注册：{name}")
            entry = {"unload": None, "closing": False, "category": category}
            self._plugins[name] = entry
        try:
            unload = self.runtime.mount(install, dependencies=dependencies, with_context=True)
        except BaseException:
            with self._lock:
                if self._plugins.get(name) is entry:
                    del self._plugins[name]
            raise
        with self._lock:
            entry["unload"] = unload

        def dispose():
            self._dispose(name, entry)
        return dispose

    def _dispose(self, name, entry):
        with self._lock:
            if self._plugins.get(name) is not entry or entry["closing"]:
                return False
            if entry["unload"] is None:
                raise RuntimeError("插件正在加载")
            entry["closing"] = True
        try:
            entry["unload"]()
        finally:
            with self._lock:
                if self._plugins.get(name) is entry:
                    del self._plugins[name]
        return True

    def unload(self, name):
        with self._lock:
            entry = self._plugins.get(name)
        return False if entry is None else self._dispose(name, entry)

    def names(self, category=None):
        if category not in (None, "tool", "prompt"):
            raise ValueError("插件类别无效")
        with self._lock:
            return tuple(sorted(name for name, entry in self._plugins.items()
                                if category is None or entry["category"] == category))
