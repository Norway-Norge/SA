"""Cordis facade over the dispatcher's tool registry."""

from cordis_py import Service


class ToolRegistryService(Service):
    def __init__(self, ctx, registry):
        self._registry = registry
        super().__init__(ctx, "tools")

    def register(self, schema, handler):
        return self._registry.register(schema, handler)

    def get(self, name):
        return self._registry.get(name)

    def snapshot(self):
        return self._registry.snapshot()

    @property
    def schemas(self):
        return self._registry.schemas
