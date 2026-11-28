"""Workspace access policy around a trusted local filesystem provider."""

from contextlib import contextmanager
from threading import RLock

from secureagent.security.workspace import Workspace, WorkspaceError
from secureagent.capabilities.filesystem.file_backend import MAX_CREATE_BYTES
from secureagent.capabilities.filesystem.file_service import FileSystem, FileTarget


class RestrictedFileSystem:
    """Enforce SA workspace rules before delegating to a filesystem provider."""

    def __init__(self, workspace: Workspace, provider: FileSystem, *, get_policy=None):
        self.workspace = workspace
        self.provider = provider
        self._get_policy = get_policy
        self._targets: dict[str, tuple[str, FileTarget]] = {}
        self._lock = RLock()

    def _remember(self, path: str, target: FileTarget) -> FileTarget:
        with self._lock:
            self._targets[target.key] = (path, target)
        return target

    def _check_target(self, target: FileTarget) -> None:
        with self._lock:
            known = self._targets.get(target.key)
        if known is None or known[1] != target:
            raise WorkspaceError("文件目标未授权")
        self._resolve_write(known[0])
        if self.provider.resolve(known[0]) != target:
            raise WorkspaceError("文件目标已变化")

    def _resolve_write(self, path):
        if self._get_policy is not None:
            policy = self._get_policy()
            if policy.resolve().mode == "read-only":
                raise WorkspaceError("只读模式不允许修改宿主文件")
            if policy.resolve().mode == "danger-full-access":
                return self.workspace.resolve_read(path)
        return self.workspace.resolve(path)

    def resolve(self, path: str) -> FileTarget:
        self._resolve_write(path)
        return self._remember(path, self.provider.resolve(path))

    @contextmanager
    def open_read(self, path: str):
        with self.provider.open_read(path) as stream:
            self._remember(path, stream.target)
            yield stream

    def create_file(self, path: str, content: bytes) -> dict:
        if len(content) > MAX_CREATE_BYTES:
            return {"error": "文件内容超过大小限制"}
        if self._get_policy is not None and self._get_policy().resolve().mode == "read-only":
            raise WorkspaceError("只读模式不允许修改宿主文件")
        if self._get_policy is not None and self._get_policy().resolve().mode == "danger-full-access":
            self.workspace.resolve_new_file_unrestricted(path)
        else:
            self.workspace.resolve_new_file(path, allow_missing_parents=True)
        return self.provider.create_file(path, content)

    def current_version(self, target: FileTarget) -> object | None:
        self._check_target(target)
        return self.provider.current_version(target)

    def read_observed(self, target: FileTarget, version: object, *, max_bytes: int | None = None) -> bytes | None:
        self._check_target(target)
        limit = MAX_CREATE_BYTES if max_bytes is None else min(max_bytes, MAX_CREATE_BYTES)
        return self.provider.read_observed(target, version, max_bytes=limit)

    def replace_observed(self, path: str, target: FileTarget, version: object,
                         content: bytes, kind: str, check=None) -> dict:
        if kind not in {"write", "edit"} or len(content) > MAX_CREATE_BYTES:
            raise ValueError("文件操作或内容无效")
        self._resolve_write(path)
        self._check_target(target)
        if self.provider.resolve(path) != target:
            raise WorkspaceError("文件目标已变化")
        if kind == "write":
            current = self.provider.read_observed(target, version, max_bytes=MAX_CREATE_BYTES)
            if current is None:
                return {"code": "STALE_VERSION", "error": "文件已变化，请重新读取"}
            if current:
                return {"code": "REVIEW_REQUIRED", "error": "非空文件写入需要审批"}

        def check_before_commit():
            try:
                self._resolve_write(path)
                self._check_target(target)
            except WorkspaceError:
                return False, "资源不在授权工作区内或已发生变化"
            return check() if check is not None else (True, "")

        return self.provider.replace_observed(path, target, version, content, kind, check_before_commit)
