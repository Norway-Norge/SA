"""Single-provider shell service."""

from threading import RLock
from cordis_py import Service


class ShellService(Service):
    def __init__(self, ctx):
        self._lock = RLock()
        self._provider = None
        super().__init__(ctx, "shell")

    def register_provider(self, provider):
        if not callable(getattr(provider, "execute", None)):
            raise ValueError("Shell provider 无效")
        with self._lock:
            if self._provider is not None:
                raise ValueError("Shell provider 已注册")
            self._provider = provider

        def dispose():
            with self._lock:
                if self._provider is provider:
                    self._provider = None
        return dispose

    def execute(self, command, timeout):
        with self._lock:
            provider = self._provider
        return provider.execute(command, timeout) if provider else {"error": "Shell provider 不可用"}
