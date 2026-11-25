"""Cordis service for local skill providers."""

from threading import RLock

from cordis_py import Service


class SkillService(Service):
    def __init__(self, ctx):
        self._lock = RLock()
        self._providers = {}
        super().__init__(ctx, "skills")

    def register_provider(self, name, provider):
        if (not isinstance(name, str) or not name.strip()
                or not callable(getattr(provider, "list", None))
                or not callable(getattr(provider, "load", None))):
            raise ValueError("Skill provider 无效")
        entry = (object(), provider)
        with self._lock:
            if name in self._providers:
                raise ValueError("Skill provider 已注册")
            self._providers[name] = entry

        def dispose():
            with self._lock:
                if self._providers.get(name) is entry:
                    del self._providers[name]
        return dispose

    def _selected(self):
        with self._lock:
            if len(self._providers) != 1:
                return None
            return next(iter(self._providers.values()))[1]

    def list(self):
        provider = self._selected()
        if provider is None:
            return {"skills": [], "issues": [{"error": "Skill provider 不可用或不唯一"}], "truncated": True}
        result = provider.list()
        return result if isinstance(result, dict) and "skills" in result else {
            "skills": [], "issues": [{"error": "Skill provider 不可用"}], "truncated": True}

    def load(self, name):
        provider = self._selected()
        return provider.load(name) if provider else {"error": "Skill provider 不可用或不唯一"}
