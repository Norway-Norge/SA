"""Search workspace text files."""

import json
import os
from pathlib import Path

from secureagent.security.workspace import Workspace, WorkspaceError, is_link_or_reparse
from secureagent.tools.spec import spec

MAX_FILE_BYTES = 256 * 1024
MAX_SCAN_FILES = 1000
MAX_MATCHES = 50
MAX_SAVED_MATCHES = 10_000

SEARCH = spec("search_files", "在本地目录内按文字搜索 UTF-8 文件。",
              {"path": {"type": "string", "description": "绝对路径或相对于工作区的目录。"}, "query": {"type": "string", "minLength": 1, "maxLength": 200}},
              ["path", "query"])


def search_files(workspace: Workspace, path: str, query: str, spill_store=None) -> dict:
    root = workspace.resolve_read(path, directory=True)
    matches: list[dict] = []
    scanned = 0
    truncated = False
    for directory, dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in {".venv", "__pycache__"}
                         and not workspace.exclude_entry(d)
                         and not is_link_or_reparse(base / d))
        for name in sorted(files):
            if workspace.exclude_entry(name):
                continue
            if scanned >= MAX_SCAN_FILES or len(matches) >= MAX_SAVED_MATCHES:
                truncated = True
                break
            scanned += 1
            candidate = base / name
            try:
                try:
                    relative = str(candidate.relative_to(workspace.root))
                except ValueError:
                    relative = str(candidate)
                target = workspace.resolve_read(str(candidate))
                target.relative_to(root)
                with target.open("rb") as stream:
                    data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) > MAX_FILE_BYTES or b"\x00" in data:
                    continue
                for number, line in enumerate(data.decode("utf-8").splitlines(), 1):
                    if query.casefold() in line.casefold():
                        matches.append({"path": relative, "line": number, "text": line[:500]})
                        if len(matches) >= MAX_SAVED_MATCHES:
                            truncated = True
                            break
            except (OSError, UnicodeDecodeError, WorkspaceError, ValueError):
                continue
        if truncated:
            break
    result = {"matches": matches[:MAX_MATCHES], "files_scanned": scanned,
              "truncated": truncated or len(matches) > MAX_MATCHES}
    if len(matches) > MAX_MATCHES and spill_store is not None:
        full = {"matches": matches, "files_scanned": scanned, "truncated": truncated,
                "note": "每条匹配文本最多 500 字符；只包含本次扫描并保留的匹配。"}
        try:
            result["spill_id"] = spill_store.save(json.dumps(full, ensure_ascii=False))
            result["read_hint"] = "使用 read_spill 分页读取本次扫描保留的全部匹配。"
        except (OSError, ValueError, UnicodeError):
            result["spill_error"] = "完整扫描结果无法保存；请缩小搜索范围。"
    return result

