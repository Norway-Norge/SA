"""Cordis service for session-scoped tool-result storage."""

from threading import RLock

from cordis_py import Service


class SpillService(Service):
    def __init__(self, ctx):
        self._lock = RLock()
        self._providers = {}
        super().__init__(ctx, "spillStore")

    def register_provider(self, name, provider):
        if (not isinstance(name, str) or not name.strip()
                or any(not callable(getattr(provider, method, None)) for method in ("save", "read", "retain"))):
            raise ValueError("Spill provider 无效")
        entry = (object(), provider)
        with self._lock:
            if name in self._providers:
                raise ValueError("Spill provider 已注册")
            self._providers[name] = entry
        def dispose():
            with self._lock:
                if self._providers.get(name) is entry:
                    del self._providers[name]
        return dispose

    def _selected(self):
        with self._lock:
            return next(iter(self._providers.values()))[1] if len(self._providers) == 1 else None

    def save(self, text):
        provider = self._selected()
        result = provider.save(text) if provider else None
        if not isinstance(result, str) or not result:
            raise ValueError("Spill provider 不可用或不唯一")
        return result

    def read(self, identifier, offset=0, limit=6000):
        provider = self._selected()
        return provider.read(identifier, offset, limit) if provider else {"error": "Spill provider 不可用或不唯一"}

    def retain(self, tool_name, result):
        provider = self._selected()
        if provider is None:
            return result
        retained = provider.retain(tool_name, result)
        return retained if isinstance(retained, dict) and "role" in retained else result
