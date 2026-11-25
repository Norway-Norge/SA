"""Trusted persona contributions; exactly one can be selected at a time."""

from threading import RLock

from cordis_py import Service


class PersonaService(Service):
    def __init__(self, ctx):
        self._lock = RLock()
        self._personas = {}
        self._selected = None
        # 将此服务注册到 Cordis 上下文，供插件访问。
        super().__init__(ctx, "personas")

    def register(self, name, prompt):
        if not isinstance(name, str) or not name.strip() or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("人格名称或提示词无效")
        entry = (object(), prompt.strip())
        with self._lock:
            if name in self._personas:
                raise ValueError(f"人格已注册：{name}")
            self._personas[name] = entry

        def dispose():
            with self._lock:
                if self._personas.get(name) is entry:
                    del self._personas[name]
                    if self._selected == name:
                        self._selected = None
        return dispose

    def select(self, name):
        with self._lock:
            if name is not None and name not in self._personas:
                raise ValueError(f"人格未注册：{name}")
            self._selected = name

    def current(self):
        with self._lock:
            if self._selected is None:
                return None
            return self._selected, self._personas[self._selected][1]

    def names(self):
        with self._lock:
            return tuple(sorted(self._personas))

    def snapshot(self):
        with self._lock:
            return {"personas": sorted(self._personas), "selected": self._selected}
