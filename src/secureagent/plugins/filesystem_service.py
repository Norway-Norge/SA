"""Cordis filesystem service with one lifecycle-owned provider."""

from contextlib import contextmanager
from threading import Condition, RLock, get_ident

from cordis_py import Service

from secureagent.security.file_access import RestrictedFileSystem
from secureagent.security.workspace import WorkspaceError


class FileSystemService(Service):
    def __init__(self, ctx, workspace, *, on_change=None):
        self.workspace = workspace
        self._condition = Condition(RLock())
        self._entry = None
        self._on_change = on_change or (lambda: None)
        self._transition = False
        self._active = 0
        self._threads = {}
        super().__init__(ctx, "fs")

    def _change(self, mutate):
        with self._condition:
            ident = get_ident()
            if ident in self._threads:
                raise RuntimeError("不能在文件操作中修改 provider")
            while self._transition:
                self._condition.wait()
            self._transition = True
            try:
                while self._active:
                    self._condition.wait()
                previous = self._entry
                result = mutate()
                if previous is not self._entry:
                    self._on_change()
                return result
            finally:
                self._transition = False
                self._condition.notify_all()

    def assert_transition_allowed(self):
        with self._condition:
            if get_ident() in self._threads:
                raise RuntimeError("不能在文件操作中重载 provider")

    def register_provider(self, provider):
        required = ("resolve", "open_read", "create_file", "read_observed", "current_version", "replace_observed")
        if any(not callable(getattr(provider, method, None)) for method in required):
            raise ValueError("文件系统 provider 无效")
        entry = {"backend": RestrictedFileSystem(self.workspace, provider)}
        def add():
            if self._entry is not None:
                raise ValueError("文件系统 provider 已注册")
            self._entry = entry
        self._change(add)

        def dispose():
            def remove():
                if self._entry is entry:
                    self._entry = None
            self._change(remove)
        return dispose

    @contextmanager
    def operation(self):
        with self._condition:
            ident = get_ident()
            while self._transition and ident not in self._threads:
                self._condition.wait()
            entry = self._entry
            if entry is None:
                raise WorkspaceError("文件系统 provider 不可用")
            self._active += 1
            self._threads[ident] = self._threads.get(ident, 0) + 1
        try:
            yield entry["backend"]
        finally:
            with self._condition:
                self._active -= 1
                self._threads[ident] -= 1
                if not self._threads[ident]:
                    del self._threads[ident]
                self._condition.notify_all()

    def resolve(self, path):
        with self.operation() as backend:
            return backend.resolve(path)

    @contextmanager
    def open_read(self, path):
        with self.operation() as backend, backend.open_read(path) as stream:
            yield stream

    def create_file(self, path, content):
        with self.operation() as backend:
            return backend.create_file(path, content)

    def read_observed(self, target, version, *, max_bytes=None):
        with self.operation() as backend:
            return backend.read_observed(target, version, max_bytes=max_bytes)

    def current_version(self, target):
        with self.operation() as backend:
            return backend.current_version(target)

    def replace_observed(self, path, target, version, content, kind, check=None):
        with self.operation() as backend:
            return backend.replace_observed(path, target, version, content, kind, check)
