"""Provider contract and local implementation for guarded file operations."""

import hashlib
import os
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ContextManager, Iterator, Protocol

from secureagent.security.observations import ReadObservation, file_identity
from secureagent.security.workspace import Workspace, WorkspaceError
from secureagent.tools.file_backend import (MAX_CREATE_BYTES, create_workspace_file,
    file_version, open_workspace_file, replace_workspace_file, write_lock)


@dataclass(frozen=True)
class FileTarget:
    # The key is provider-owned. Consumers must not interpret it as a path.
    key: str
    display_path: str


@dataclass(frozen=True)
class LocalVersion:
    digest: str
    identity: tuple[int, int, int, int, int]


class ReadStream(Protocol):
    """A bounded-chunk read; finish returns a version only after a stable complete read."""
    target: FileTarget
    def chunks(self) -> Iterator[bytes]: ...
    def finish(self) -> tuple[object, int] | None: ...


class FileSystem(Protocol):
    """Trusted provider contract; versions and target keys are provider-owned.

    replace_observed must check the version immediately before publication or
    refuse the write; this does not imply cross-process atomic compare-and-swap.
    An uncertain mutation must not be retried by the provider. This interface
    alone does not provide authorization or a remote workspace boundary.
    """
    def resolve(self, path: str) -> FileTarget: ...
    def open_read(self, path: str) -> ContextManager[ReadStream]: ...
    def create_file(self, path: str, content: bytes) -> dict: ...
    def read_observed(self, target: FileTarget, version: object, *, max_bytes: int | None = None) -> bytes | None: ...
    def current_version(self, target: FileTarget) -> object | None: ...
    def replace_observed(self, path: str, target: FileTarget, version: object,
                         content: bytes, kind: str,
                         check: Callable[[], tuple[bool, str]] | None = None) -> dict: ...


class _LocalReadStream:
    def __init__(self, target: Path, stream):
        self.target = FileTarget(str(target), str(target))
        self._stream = stream
        self._before = file_identity(os.fstat(stream.fileno()))
        self._digest = hashlib.sha256()
        self._complete = False

    def chunks(self) -> Iterator[bytes]:
        while chunk := self._stream.read(64 * 1024):
            self._digest.update(chunk)
            yield chunk
        self._complete = True

    def finish(self) -> tuple[object, int] | None:
        if not self._complete:
            return None
        after = file_identity(os.fstat(self._stream.fileno()))
        if self._before != after:
            return None
        return LocalVersion(self._digest.hexdigest(), after), after[2]


def local_observation_parts(observation: ReadObservation) -> tuple[str, tuple[int, int, int, int, int]] | None:
    version = observation.version
    if not isinstance(version, LocalVersion):
        return None
    return version.digest, version.identity


def observed_bytes(target: Path, observation: ReadObservation, *, max_bytes: int | None = MAX_CREATE_BYTES) -> bytes | None:
    expected = local_observation_parts(observation)
    if expected is None:
        return None
    with target.open("rb") as stream:
        if file_identity(os.fstat(stream.fileno())) != expected[1]:
            return None
        raw = stream.read() if max_bytes is None else stream.read(max_bytes + 1)
        after = file_identity(os.fstat(stream.fileno()))
    if ((max_bytes is not None and len(raw) > max_bytes) or after != expected[1]
            or file_identity(target.stat()) != after
            or hashlib.sha256(raw).hexdigest() != expected[0]):
        return None
    return raw


class LocalFileSystem:
    """Host filesystem operations; access is subject to OS process permissions."""

    def __init__(self, workspace: Workspace | Path):
        base = workspace.root if isinstance(workspace, Workspace) else Path(workspace)
        self.workspace = _LocalPaths(base)

    def resolve(self, path: str) -> FileTarget:
        target = self.workspace.resolve(path)
        return FileTarget(str(target), str(target))

    @contextmanager
    def open_read(self, path: str) -> Iterator[ReadStream]:
        with open_workspace_file(self.workspace, path) as (target, stream):
            yield _LocalReadStream(target, stream)

    def create_file(self, path: str, content: bytes) -> dict:
        return create_workspace_file(self.workspace, path, content)

    def _path(self, target: FileTarget) -> Path:
        return Path(target.key)

    def current_version(self, target: FileTarget) -> object | None:
        version = file_version(self._path(target), max_bytes=None)
        return LocalVersion(*version) if version is not None else None

    def read_observed(self, target: FileTarget, version: object, *, max_bytes: int | None = None) -> bytes | None:
        if not isinstance(version, LocalVersion):
            return None
        observation = ReadObservation(target.key, version, version.identity[2], 1, 0, 0, True)
        return observed_bytes(self._path(target), observation, max_bytes=max_bytes)

    def replace_observed(self, path: str, target: FileTarget, version: object,
                         content: bytes, kind: str,
                         check: Callable[[], tuple[bool, str]] | None = None) -> dict:
        if kind not in {"write", "edit"}:
            raise ValueError("文件操作或内容无效")
        if not isinstance(version, LocalVersion):
            return {"code": "STALE_VERSION", "error": "文件版本不属于此提供方"}
        with write_lock:
            if self.resolve(path) != target or self.current_version(target) != version:
                return {"code": "STALE_VERSION", "error": "文件已变化，请重新读取"}
            return self._replace(path, target, version, content, kind, check)

    def _replace(self, path: str, target: FileTarget, version: LocalVersion,
                 content: bytes, kind: str, check: Callable[[], tuple[bool, str]] | None) -> dict:
        target_path = self._path(target)
        mode = stat.S_IMODE(target_path.stat().st_mode)
        fd, staged = tempfile.mkstemp(prefix=f".sa-{kind}-", dir=target_path.parent)
        result = None
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name != "nt":
                os.chmod(staged, mode)
            allowed, reason = check() if check is not None else (True, "")
            if not allowed:
                result = {"code": "DENIED", "error": reason}
            elif self.resolve(path) != target or self.current_version(target) != version:
                result = {"code": "STALE_VERSION", "error": "文件已变化，请重新读取"}
            else:
                try:
                    replace_workspace_file(target_path, staged)
                except OSError:
                    result = {"status": "UNKNOWN", "error": "写入结果未知，请先核对文件"}
                else:
                    result = {"status": "updated", "path": path}
        finally:
            try:
                if os.path.lexists(staged):
                    os.unlink(staged)
            except OSError:
                if result is not None:
                    result["warning"] = "临时文件清理未确认"
        return result


class _LocalPaths:
    """Local path resolution without a workspace access policy."""

    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)

    def _candidate(self, value: str) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise WorkspaceError("路径无效")
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    def resolve(self, value: str, *, directory: bool = False) -> Path:
        try:
            target = self._candidate(value).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspaceError("路径不存在") from exc
        if directory and not target.is_dir():
            raise WorkspaceError("目标不是目录")
        if not directory and not target.is_file():
            raise WorkspaceError("目标不是文件")
        return target

    def resolve_new_file(self, value: str, *, allow_missing_parents: bool = False) -> Path:
        try:
            target = self._candidate(value).resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspaceError("路径无效") from exc
        if os.path.lexists(target):
            raise WorkspaceError("目标已存在")
        if not allow_missing_parents and not target.parent.is_dir():
            raise WorkspaceError("父目录不存在")
        return target
