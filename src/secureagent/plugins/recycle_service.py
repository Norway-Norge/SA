"""Provider facade for reviewed file removal, conversion and recovery."""

from contextlib import contextmanager
from threading import Condition, RLock, get_ident

from cordis_py import Service

from secureagent.security.workspace import WorkspaceError
from secureagent.tools.file_service import LocalFileSystem


class RecycleService(Service):
    def __init__(self, ctx):
        self._condition = Condition(RLock())
        self._entry = None
        self._used = False
        self.file_system = None
        super().__init__(ctx, "recycle")

    def register_provider(self, name, provider):
        methods = ("_resolve_target", "_valid_id", "_authorize_convert", "bind_decisions", "_decide",
                   "propose_delete", "propose_restore", "propose_convert", "list_items", "decide", "status")
        if (not isinstance(name, str) or not name.strip()
                or any(not callable(getattr(provider, method, None)) for method in methods)):
            raise ValueError("回收站 provider 无效")
        entry = {"provider": provider, "active": 0, "threads": {}, "closing": False}
        with self._condition:
            if self._used:
                raise ValueError("回收站 provider 不支持在运行时替换；请重建工作区")
            self._used = True
            self._entry = entry

        def dispose():
            with self._condition:
                if self._entry is not entry or entry["closing"]:
                    return
                if get_ident() in entry["threads"]:
                    raise RuntimeError("不能从回收站 provider 的调用中卸载自身")
                entry["closing"] = True
                self._entry = None
                while entry["active"]:
                    self._condition.wait()
        return dispose

    @contextmanager
    def _lease(self):
        with self._condition:
            entry = self._entry
            if entry is None or entry["closing"]:
                raise WorkspaceError("回收站 provider 不可用")
            ident = get_ident()
            entry["active"] += 1
            entry["threads"][ident] = entry["threads"].get(ident, 0) + 1
        try:
            yield entry["provider"]
        finally:
            with self._condition:
                entry["active"] -= 1
                entry["threads"][ident] -= 1
                if not entry["threads"][ident]:
                    del entry["threads"][ident]
                self._condition.notify_all()

    def _call(self, method, *args):
        with self._lease() as provider:
            return getattr(provider, method)(*args)

    def _resolve_target(self, path):
        return self._call("_resolve_target", path)

    def _valid_id(self, recycle_id):
        return self._call("_valid_id", recycle_id)

    def _authorize_convert(self, args):
        return self._call("_authorize_convert", args)

    def bind_decisions(self, actions, handler):
        return self._call("bind_decisions", actions, handler)

    def _decide(self, approval_id, approve):
        return self._call("_decide", approval_id, approve)

    def propose_delete(self, path):
        return self._call("propose_delete", path)

    def propose_restore(self, recycle_id):
        return self._call("propose_restore", recycle_id)

    def propose_convert(self, source_path, target_path, content):
        return self._call("propose_convert", source_path, target_path, content)

    def list_items(self):
        return self._call("list_items")

    def decide(self, approval_id, approve):
        try:
            if self.file_system is None:
                return self._call("decide", approval_id, approve)
            with self.file_system.operation() as backend:
                if not isinstance(backend.provider, LocalFileSystem):
                    raise WorkspaceError("当前文件系统 provider 不支持本地回收操作")
                return self._call("decide", approval_id, approve)
        except WorkspaceError:
            return {"code": "INVALID_APPROVAL", "error": "回收站 provider 不可用"}

    def status(self, approval_id):
        try:
            return self._call("status", approval_id)
        except WorkspaceError:
            return {"status": "UNKNOWN", "message": "回收站 provider 不可用"}
