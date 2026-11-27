"""Monotonic execution guards; a mandatory authorization owner fails closed."""

from threading import RLock


class ToolGuards:
    def __init__(self):
        self._lock = RLock()
        self._authorizer = None
        self._guards = []
        self._revision = 0

    def register_authorizer(self, guard):
        if not callable(guard):
            raise TypeError("授权检查必须可调用")
        entry = (object(), guard)
        with self._lock:
            if self._authorizer is not None:
                raise ValueError("强制授权检查已注册")
            self._authorizer = entry
            self._revision += 1
        def dispose():
            with self._lock:
                if self._authorizer is entry:
                    self._authorizer = None
                    self._revision += 1
        return dispose

    def register(self, guard):
        if not callable(guard):
            raise TypeError("安全守卫必须可调用")
        entry = (object(), guard)
        with self._lock:
            self._guards.append(entry)
            self._revision += 1
        def dispose():
            with self._lock:
                if entry in self._guards:
                    self._guards.remove(entry)
                    self._revision += 1
        return dispose

    @property
    def revision(self):
        with self._lock:
            return self._revision

    def check(self, call):
        with self._lock:
            owner, guards = self._authorizer, tuple(self._guards)
        if owner is None:
            return False, "强制授权策略不可用"
        try:
            for entry in (owner, *guards):
                result = entry[1](call)
                if result is not None:
                    if not isinstance(result, str) or not result:
                        return False, "安全守卫返回无效结果"
                    return False, result
            with self._lock:
                if self._authorizer is not owner or tuple(self._guards) != guards:
                    return False, "安全策略已变化，请重新发起调用"
            return True, "允许"
        except Exception:
            return False, "安全策略检查失败"
