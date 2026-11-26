"""Cordis facade over the dispatcher's tool registry."""

from cordis_py import Service
from secureagent.security.pre_execute import PreExecutePolicies
from secureagent.security.tool_guards import ToolGuards
from secureagent.security.post_execute import PostExecutePolicies


class ToolRegistryService(Service):
    def __init__(self, ctx, registry):
        self._registry = registry
        self._pre_execute = PreExecutePolicies()
        self._guards = ToolGuards()
        self._post_execute = PostExecutePolicies()
        # 将此服务注册到 Cordis 上下文，供插件访问。
        super().__init__(ctx, "tools")

    def register(self, schema, handler):
        return self._registry.register(schema, handler)

    def get(self, name):
        return self._registry.get(name)

    def on_pre_execute(self, hook):
        """Register a trusted pre-execute hook and return its disposer."""
        return self._pre_execute.register(hook)

    def pre_execute_event_changed(self):
        self._pre_execute.event_changed()

    def register_pre_execute_approval(self, handler):
        return self._pre_execute.register_approval(handler)

    def pre_execute(self, invocation):
        return self._pre_execute.evaluate(invocation, self.ctx)

    def register_authorizer(self, guard):
        """Install the required authorization owner; its absence denies calls."""
        return self._guards.register_authorizer(guard)

    def guard(self, guard):
        """Register a monotonic deny-or-abstain safety guard."""
        return self._guards.register(guard)

    def check_guards(self, invocation):
        return self._guards.check(invocation)

    def post_execute_event_changed(self):
        self._post_execute.event_changed()

    def register_output_filter(self, handler):
        return self._post_execute.register_filter(handler)

    def post_execute(self, invocation, result):
        return self._post_execute.process(self.ctx, invocation, result)

    def snapshot(self):
        return self._registry.snapshot()

    @property
    def schemas(self):
        return self._registry.schemas
