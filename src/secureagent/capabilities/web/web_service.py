"""Cordis web capability with independent search and fetch providers."""

import os
from threading import RLock

from cordis_py import Service


class WebService(Service):
    def __init__(self, ctx, *, search_provider=None, fetch_provider=None):
        self._lock = RLock()
        self._search = {}
        self._fetch = {}
        self.search_provider = search_provider or os.environ.get("SA_WEB_SEARCH_PROVIDER") or None
        self.fetch_provider = fetch_provider or os.environ.get("SA_WEB_FETCH_PROVIDER") or None
        # 将此服务注册到 Cordis 上下文，供插件访问。
        super().__init__(ctx, "web")

    def _register(self, store, provider_id, provider, method):
        if (not isinstance(provider_id, str) or not provider_id or len(provider_id) > 128
                or any(not (char.isascii() and (char.isalnum() or char in "-_")) for char in provider_id)
                or not callable(getattr(provider, method, None))):
            raise ValueError("网页提供方无效")
        entry = (object(), provider)
        with self._lock:
            if provider_id in store:
                raise ValueError("网页提供方已注册")
            store[provider_id] = entry

        def dispose():
            with self._lock:
                if store.get(provider_id) is entry:
                    del store[provider_id]
        return dispose

    def register_search_provider(self, provider_id, provider):
        return self._register(self._search, provider_id, provider, "search")

    def register_fetch_provider(self, provider_id, provider):
        return self._register(self._fetch, provider_id, provider, "fetch")

    def _select(self, store, configured):
        with self._lock:
            if configured is not None:
                entry = store.get(configured)
                if entry is None:
                    return None, {"error": "配置的网页提供方未注册", "code": "WEB_PROVIDER_CONFIGURED_MISSING"}
                return (configured, entry[1]), None
            if not store:
                return None, {"error": "没有可用的网页提供方", "code": "WEB_PROVIDER_UNAVAILABLE"}
            if len(store) != 1:
                return None, {"error": "网页提供方不唯一，请明确配置", "code": "WEB_PROVIDER_AMBIGUOUS"}
            provider_id, entry = next(iter(store.items()))
            return (provider_id, entry[1]), None

    def search(self, query, *, max_results):
        selected, error = self._select(self._search, self.search_provider)
        return error if error else selected[1].search(query, max_results=max_results)

    def fetch(self, url):
        selected, error = self._select(self._fetch, self.fetch_provider)
        if error:
            return error
        provider_id, provider = selected
        result = provider.fetch(url)
        return {**result, "provider_id": provider_id} if "error" not in result else result
