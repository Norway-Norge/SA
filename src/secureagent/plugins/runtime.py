"""Lifecycle adapter for explicitly installed trusted in-process plugins."""

import asyncio
from threading import Condition, RLock, Thread, get_ident

from cordis_py import Context, FiberState
from secureagent.plugins.tool_service import ToolRegistryService


def _run_sync_lifecycle(operation):
    """Run Cordis' synchronous lifecycle outside an active asyncio loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return operation()
    result = []
    errors = []
    def run():
        try:
            result.append(operation())
        except BaseException as exc:
            errors.append(exc)
    thread = Thread(target=run)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]


class PluginRegistration:
    def __init__(self, registry, policy):
        self.registry = registry
        self.policy = policy
        self._condition = Condition(RLock())
        self._closing = False
        self._closed = False
        self._active = 0
        self._threads = {}
        self._tools = []
        self._rules = []
        self._cleanups = []
        self.errors = []

    def register_cleanup(self, dispose):
        with self._condition:
            if self._closing:
                raise RuntimeError("插件正在卸载")
            self._cleanups.append(dispose)

    def guard(self, handler):
        def guarded(*args, **kwargs):
            with self._condition:
                if self._closing:
                    return {"error": "插件正在卸载"}
                self._active += 1
                ident = get_ident()
                self._threads[ident] = self._threads.get(ident, 0) + 1
            try:
                return handler(*args, **kwargs)
            finally:
                with self._condition:
                    self._active -= 1
                    self._threads[ident] -= 1
                    if not self._threads[ident]:
                        del self._threads[ident]
                    self._condition.notify_all()

        return guarded

    def register(self, schema, handler):
        with self._condition:
            if self._closing:
                raise RuntimeError("插件正在卸载")
            dispose = self.registry.register(schema, self.guard(handler))
            self._tools.append(dispose)
            return dispose

    def register_rule(self, name, check, *, writes=False):
        with self._condition:
            if self._closing:
                raise RuntimeError("插件正在卸载")
            dispose = self.policy.register_rule(name, check, writes=writes)
            self._rules.append(dispose)
            return dispose

    def close(self):
        with self._condition:
            if get_ident() in self._threads:
                raise RuntimeError("不能从插件自身的工具调用中卸载插件")
            if self._closing:
                while not self._closed:
                    self._condition.wait()
                return
            self._closing = True
            for dispose in reversed(self._tools):
                try:
                    dispose()
                except Exception as exc:
                    self.errors.append(exc)
            while self._active:
                self._condition.wait()
            for dispose in reversed(self._rules):
                try:
                    dispose()
                except Exception as exc:
                    self.errors.append(exc)
            for dispose in reversed(self._cleanups):
                try:
                    dispose()
                except Exception as exc:
                    self.errors.append(exc)
            self._closed = True
            self._condition.notify_all()


class TrustedPluginRuntime:
    """Mount explicit trusted plugins; never imports model-supplied code."""

    def __init__(self, registry, policy):
        self.context = Context()
        self.registry = ToolRegistryService(self.context, registry)
        self.policy = policy
        self._lifecycle_lock = RLock()

    def mount(self, register, *, dependencies=(), with_context=False):
        scope = PluginRegistration(self.registry, self.policy)
        startup_errors = []

        def component(ctx, config):
            ctx.effect(lambda: scope.close)
            try:
                if with_context:
                    register(scope, ctx)
                else:
                    register(scope)
            except BaseException as exc:
                scope.close()
                if scope.errors:
                    failure = RuntimeError("插件启动失败且回滚未完成")
                    failure.__cause__ = exc
                    startup_errors.append(failure)
                else:
                    startup_errors.append(exc)

        component.inject = list(dependencies)

        def activate():
            with self._lifecycle_lock:
                fiber = self.context.plugin(component)
                if startup_errors:
                    fiber.dispose_sync()
                    raise startup_errors[0]
                if fiber.state != FiberState.ACTIVE:
                    fiber.dispose_sync()
                    raise RuntimeError("插件所需服务不可用")
                return fiber
        fiber = _run_sync_lifecycle(activate)

        def unload():
            # Explicit close surfaces cleanup errors suppressed by Cordis.
            scope.close()
            def dispose():
                with self._lifecycle_lock:
                    fiber.dispose_sync()
            _run_sync_lifecycle(dispose)
            if scope.errors:
                raise RuntimeError("插件清理未完成") from scope.errors[0]

        return unload
