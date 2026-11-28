"""Resolve paths while enforcing workspace containment and link rules."""

from pathlib import Path, PureWindowsPath
import stat
from threading import RLock


def is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return False


class WorkspaceError(ValueError):
    pass


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise WorkspaceError("工作区必须是目录")
        self._entry_filters = []
        self._filter_lock = RLock()

    def register_entry_filter(self, callback):
        if not callable(callback):
            raise TypeError("目录条目过滤器必须可调用")
        entry = (object(), callback)
        with self._filter_lock:
            self._entry_filters.append(entry)
        def dispose():
            with self._filter_lock:
                if entry in self._entry_filters:
                    self._entry_filters.remove(entry)
        return dispose

    def exclude_entry(self, name):
        with self._filter_lock:
            entries = tuple(self._entry_filters)
        for _, callback in entries:
            try:
                result = callback(name)
                if result is not False:
                    return True
            except Exception:
                return True
        return False

    def _require_containment(self, candidate: Path) -> None:
        # Resolve the actual target, independently from lexical policy plugins.
        try:
            (self.root / candidate).resolve(strict=False).relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspaceError("路径不存在或不在工作区内") from exc

    def resolve_read(self, value: str, *, directory: bool = False) -> Path:
        """Resolve a readable local path; optional tool guards select its scope."""
        if not isinstance(value, str) or not value or "\x00" in value:
            raise WorkspaceError("路径无效")
        try:
            candidate = Path(value)
            resolved = (self.root / candidate).resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspaceError("路径不存在或无法访问") from exc
        if directory and not resolved.is_dir():
            raise WorkspaceError("目标不是目录")
        if not directory and not resolved.is_file():
            raise WorkspaceError("目标不是文件")
        return resolved

    def resolve_new_file_unrestricted(self, value: str) -> Path:
        """Resolve a new local target without applying a workspace boundary."""
        import os
        if not isinstance(value, str) or not value or "\x00" in value:
            raise WorkspaceError("路径无效")
        try:
            target = (self.root / Path(value)).resolve(strict=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspaceError("路径无效") from exc
        if os.path.lexists(target):
            raise WorkspaceError("目标已存在")
        return target

    def resolve(self, value: str, *, directory: bool = False) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise WorkspaceError("路径无效")
        candidate = Path(value)
        if candidate.is_absolute() or candidate.drive:
            raise WorkspaceError("只允许工作区内的相对路径")
        self._require_containment(candidate)
        current = self.root
        try:
            for part in candidate.parts:
                current = current / part
                if is_link_or_reparse(current):
                    raise WorkspaceError("不允许访问符号链接或连接点")
            resolved = current.resolve(strict=True)
            resolved.relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspaceError("路径不存在或不在工作区内") from exc
        if directory and not resolved.is_dir():
            raise WorkspaceError("目标不是目录")
        if not directory and not resolved.is_file():
            raise WorkspaceError("目标不是文件")
        return resolved

    def resolve_new_file(self, value: str, *, allow_missing_parents: bool = False) -> Path:
        """Validate a new path without following links, optionally allowing missing parents."""
        import os

        if not isinstance(value, str) or not value or "\x00" in value:
            raise WorkspaceError("路径无效")
        candidate = Path(value)
        if (candidate.is_absolute() or candidate.drive or candidate.root
                or not candidate.name
                or ":" in value or any(PureWindowsPath(part).is_reserved() or part.endswith((".", " "))
                                       for part in candidate.parts)):
            raise WorkspaceError("只允许工作区内的有效相对路径")
        self._require_containment(candidate)
        if allow_missing_parents:
            parent = self.root
            for part in candidate.parent.parts:
                parent = parent / part
                if os.path.lexists(parent):
                    if is_link_or_reparse(parent) or not parent.is_dir():
                        raise WorkspaceError("父目录无效或包含符号链接")
                    try:
                        parent = parent.resolve(strict=True)
                        parent.relative_to(self.root)
                    except (OSError, RuntimeError, ValueError) as exc:
                        raise WorkspaceError("父目录不在工作区内") from exc
        else:
            parent = self.resolve(str(candidate.parent), directory=True)
        target = parent / candidate.name
        if os.path.lexists(target):
            raise WorkspaceError("目标已存在")
        return target
