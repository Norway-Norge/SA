"""Cordis service for configured isolated test execution providers."""

from threading import RLock

from cordis_py import Service


class TestRunnerService(Service):
    def __init__(self, ctx):
        self._lock = RLock()
        self._providers = {}
        super().__init__(ctx, "testRunner")

    def register_provider(self, name, provider):
        if (not isinstance(name, str) or not name.strip()
                or any(not callable(getattr(provider, method, None)) for method in ("run", "run_inline"))):
            raise ValueError("测试 provider 无效")
        entry = (object(), provider)
        with self._lock:
            if name in self._providers:
                raise ValueError("测试 provider 已注册")
            self._providers[name] = entry
        def dispose():
            with self._lock:
                if self._providers.get(name) is entry:
                    del self._providers[name]
        return dispose

    def _selected(self):
        with self._lock:
            return next(iter(self._providers.values()))[1] if len(self._providers) == 1 else None

    def run(self, path, runner):
        provider = self._selected()
        return provider.run(path, runner) if provider else {"error": "测试 provider 不可用或不唯一"}

    def run_inline(self, code, tests):
        provider = self._selected()
        return provider.run_inline(code, tests) if provider else {"error": "测试 provider 不可用或不唯一"}
