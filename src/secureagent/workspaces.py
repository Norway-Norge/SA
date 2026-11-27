"""User-selected local workspaces and their private SA storage directories."""
"""定位工作区"""

import json
import os
import tempfile
import threading
import uuid
from pathlib import Path

from secureagent.security.workspace import Workspace, WorkspaceError, is_link_or_reparse


def workspace_storage(root: Path, child: str) -> Path:
    if child not in {"sessions", "cache", "operations"}:
        raise ValueError("无效存储目录")
    root = Workspace(root).root
    parent = root / ".secureagent"
    for path in (parent, parent / child):
        if os.path.lexists(path):
            if is_link_or_reparse(path) or not path.is_dir():
                raise WorkspaceError("工作区存储目录不是安全的普通目录")
        else:
            path.mkdir(mode=0o700)
        if not path.resolve(strict=True).is_relative_to(root):
            raise WorkspaceError("工作区存储目录越界")
    return parent / child


def choose_directory() -> str | None:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        return filedialog.askdirectory(parent=root, title="选择工作区文件夹", mustexist=True) or None
    finally:
        root.destroy()


class WorkspaceRegistry:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not all(
                isinstance(k, str) and isinstance(v, str) and Path(v).is_absolute()
                and k == str(uuid.uuid5(uuid.NAMESPACE_URL, os.path.normcase(v)))
                for k, v in data.items()
            ):
                raise ValueError("invalid registry")
            return data
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise WorkspaceError("无法读取工作区列表") from exc

    def register(self, path: str) -> dict[str, str]:
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise WorkspaceError("请选择本地绝对目录")
        try:
            root = Workspace(Path(path)).root
            workspace_storage(root, "sessions")
            workspace_storage(root, "cache")
        except (OSError, ValueError) as exc:
            raise WorkspaceError("工作区不可用或无法创建存储目录") from exc
        identity = str(uuid.uuid5(uuid.NAMESPACE_URL, os.path.normcase(str(root))))
        with self.lock:
            data = self._read()
            if data.get(identity) != str(root):
                data[identity] = str(root)
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                temp_name = None
                try:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, delete=False) as stream:
                        temp_name = stream.name
                        json.dump(data, stream, ensure_ascii=False)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(temp_name, self.path)
                finally:
                    if temp_name and os.path.exists(temp_name):
                        os.unlink(temp_name)
        return {"id": identity, "path": str(root), "name": root.name or str(root)}

    def list(self) -> list[dict[str, str]]:
        with self.lock:
            data = self._read()
        return [{"id": identity, "path": path, "name": Path(path).name or path} for identity, path in data.items()]

    def resolve(self, identity: str) -> Path:
        with self.lock:
            path = self._read().get(identity)
        if path is None:
            raise WorkspaceError("工作区不存在，请重新选择")
        try:
            root = Workspace(Path(path)).root
            if os.path.normcase(str(root)) != os.path.normcase(path):
                raise WorkspaceError("工作区路径已变化")
            return root
        except (OSError, ValueError) as exc:
            raise WorkspaceError("工作区不可用") from exc
