"""Provider facade for bounded workspace file enumeration and search."""

from threading import RLock

from cordis_py import Service


class FileSearchService(Service):
    def __init__(self, ctx):
        self._lock = RLock()
        self._providers = {}
        # 将此服务注册到 Cordis 上下文，供插件访问。
        super().__init__(ctx, "fileSearch")

    def register_provider(self, name, provider):
        if (not isinstance(name, str) or not name.strip()
                or any(not callable(getattr(provider, method, None)) for method in ("search", "glob"))):
            raise ValueError("文件搜索 provider 无效")
        entry = (object(), provider)
        with self._lock:
            if name in self._providers:
                raise ValueError("文件搜索 provider 已注册")
            self._providers[name] = entry
        def dispose():
            with self._lock:
                if self._providers.get(name) is entry:
                    del self._providers[name]
        return dispose

    def _selected(self):
        with self._lock:
            return next(iter(self._providers.values()))[1] if len(self._providers) == 1 else None

    def search(self, path, query):
        provider = self._selected()
        return provider.search(path, query) if provider else {"error": "文件搜索 provider 不可用或不唯一"}

    def glob(self, pattern, path="."):
        provider = self._selected()
        return provider.glob(pattern, path) if provider else {"error": "文件搜索 provider 不可用或不唯一"}
