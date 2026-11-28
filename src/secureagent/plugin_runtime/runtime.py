"""Lifecycle adapter for explicitly installed trusted in-process plugins."""

import asyncio
import inspect
from threading import Condition, RLock, Thread, get_ident

from cordis_py import Context, FiberState
from secureagent.services.tool_service import ToolRegistryService


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
        self._event_disposers = []
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
            # 注册受生命周期保护的工具，并保存注销函数。
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

    def register_pre_execute(self, hook):
        """Bind a policy listener to this plugin's lifecycle."""
        with self._condition:
            if self._closing:
                raise RuntimeError("插件正在卸载")
            dispose = self.registry.on_pre_execute(self.guard(hook))
            self._cleanups.append(dispose)
            return dispose

    def register_pre_execute_approval(self, handler):
        with self._condition:
            if self._closing:
                raise RuntimeError("插件正在卸载")
            dispose = self.registry.register_pre_execute_approval(self.guard(handler))
            self._cleanups.append(dispose)
            return dispose

    def register_guard(self, handler):
        """Register a lifecycle-bound monotonic security guard."""
        with self._condition:
            if self._closing:
                raise RuntimeError("插件正在卸载")
            dispose = self.registry.guard(self.guard(handler))
            self._cleanups.append(dispose)
            return dispose

    def on(self, ctx, event, handler, *, prepend=False, global_=False):
        """Bind Cordis listeners to this plugin and protect active policy calls."""
        if not callable(handler):
            raise TypeError("监听函数必须可调用")
        if event == "tools/result":
            if inspect.iscoroutinefunction(handler) or inspect.iscoroutinefunction(getattr(handler, "__call__", None)):
                raise TypeError("tools/result 当前只支持同步监听函数")

            def observe(*args):
                result = handler(*args)
                if inspect.isawaitable(result):
                    close = getattr(result, "close", None)
                    if callable(close):
                        close()
                    raise TypeError("tools/result 当前只支持同步监听函数")

            with self._condition:
                if self._closing:
                    raise RuntimeError("插件正在卸载")
                undo = ctx.on(event, self.guard(observe), prepend=prepend, global_=global_)
                self._event_disposers.append(undo)
                return undo
        if event not in {"tools/pre-execute", "tools/execute", "tools/post-execute"}:
            return ctx.on(event, handler, prepend=prepend, global_=global_)

        async def listener(*values):
            *event_args, next_hook = values
            with self._condition:
                if self._closing:
                    raise RuntimeError("插件正在卸载")
                self._active += 1
                ident = get_ident()
                self._threads[ident] = self._threads.get(ident, 0) + 1
            called = False
            def next_once():
                nonlocal called
                if called:
                    raise ValueError("事件监听器不能重复调用 next")
                called = True
                return next_hook()
            try:
                result = handler(*event_args, next_once)
                return await result if inspect.isawaitable(result) else result
            finally:
                with self._condition:
                    self._active -= 1
                    self._threads[ident] -= 1
                    if not self._threads[ident]:
                        del self._threads[ident]
                    self._condition.notify_all()

        with self._condition:
            if self._closing:
                raise RuntimeError("插件正在卸载")
            undo = ctx.on(event, listener, prepend=prepend, global_=global_)
            changed = (self.registry.pre_execute_event_changed if event == "tools/pre-execute"
                       else self.registry.post_execute_event_changed if event == "tools/post-execute" else None)
            if changed is not None:
                changed()
            disposed = False
            def dispose():
                nonlocal disposed
                with self._condition:
                    if not disposed:
                        disposed = True
                        undo()
                        if changed is not None:
                            changed()
            self._event_disposers.append(dispose)
            return dispose

    def close(self):
        with self._condition:
            if get_ident() in self._threads:
                raise RuntimeError("不能从插件自身的工具调用中卸载插件")
            if self._closing:
                while not self._closed:
                    self._condition.wait()
                return
            # 先停止接收新工具调用，再等待已开始的调用完成并清理资源。
            self._closing = True
            # Withdraw listeners before waiting so pending decisions detect changes.
            for dispose in reversed(self._event_disposers):
                try:
                    dispose()
                except Exception as exc:
                    self.errors.append(exc)
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


class PluginContext:
    """Expose Cordis context while binding pre-execute listeners to the plugin scope."""

    def __init__(self, context, scope):
        self._context = context
        self._scope = scope

    def on(self, event, handler, *, prepend=False, global_=False):
        return self._scope.on(self._context, event, handler, prepend=prepend, global_=global_)

    def __getattr__(self, name):
        return getattr(self._context, name)


class TrustedPluginRuntime:
    """Mount explicit trusted plugins; never imports model-supplied code."""

    def __init__(self, registry, policy, *, context=None):
        self.context = context if context is not None else Context()
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
                    register(scope, PluginContext(ctx, scope))
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
                # 在 Cordis 中挂载插件，并检查依赖及启动结果。
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
