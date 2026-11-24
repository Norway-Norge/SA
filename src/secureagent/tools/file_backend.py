"""Trusted local file access primitive for existing workspace files."""

from contextlib import contextmanager
import hashlib
import os
import tempfile
import threading

from secureagent.security.workspace import Workspace, WorkspaceError
from secureagent.security.observations import file_identity
from pathlib import Path

MAX_CREATE_BYTES = 256 * 1024

# Serializes cooperating writes in this SA process, not external writers.
write_lock = threading.RLock()


def file_version(target: Path, max_bytes: int | None = MAX_CREATE_BYTES) -> tuple[str, tuple[int, int, int, int, int]] | None:
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        first_stat = os.fstat(stream.fileno())
        if max_bytes is not None and first_stat.st_size > max_bytes:
            return None
        before = file_identity(first_stat)
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
            if max_bytes is not None and stream.tell() > max_bytes:
                return None
        after = file_identity(os.fstat(stream.fileno()))
    if before != after or file_identity(target.stat()) != after:
        return None
    return digest.hexdigest(), after


def replace_workspace_file(target: Path, staged: str) -> None:
    if os.name != "nt":
        os.replace(staged, target)
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    replace = kernel.ReplaceFileW
    replace.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                        wintypes.DWORD, wintypes.LPVOID, wintypes.LPVOID]
    replace.restype = wintypes.BOOL
    if not replace(str(target), staged, None, 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def open_workspace_file(workspace: Workspace, path: str):
    target = workspace.resolve(path)
    with target.open("rb") as stream:
        yield target, stream


def create_workspace_file(workspace: Workspace, path: str, content: bytes) -> dict:
    """Stage content in the destination directory; publish only if absent.

    Hard-link creation fails when the destination exists, including a concurrent
    creator. Directory replacement by another local process is not prevented.
    """
    workspace.resolve_new_file(path, allow_missing_parents=True)
    parent = Path(path).parent
    current = Path(parent.anchor) if parent.is_absolute() else Path(".")
    parts = parent.parts[1:] if parent.is_absolute() else parent.parts
    created_directories = []
    for component in parts:
        checked_parent = workspace.resolve(str(current), directory=True)
        current = current / component
        candidate = checked_parent / component
        try:
            candidate.mkdir()
            created_directories.append(current.as_posix())
        except FileExistsError:
            pass
        except OSError:
            return {"status": "UNKNOWN", "error": "创建父目录的结果未知，请检查工作区后再操作",
                    "created_directories": created_directories}
        try:
            workspace.resolve(str(current), directory=True)
        except WorkspaceError:
            return {"error": "父目录无效或已发生变化", "created_directories": created_directories}
    target = workspace.resolve_new_file(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".sa-create-", dir=target.parent)
    result = None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        checked = workspace.resolve_new_file(path)
        if checked != target:
            result = {"error": "目标目录已发生变化"}
        else:
            try:
                os.link(temporary, target)
            except FileExistsError:
                result = {"error": "目标已存在"}
            except OSError:
                result = {"status": "UNKNOWN", "error": "创建结果未知，请先核对目标文件"}
            else:
                result = {"status": "created", "path": path}
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            if result is not None:
                result["warning"] = "临时文件清理未确认"
    return result
