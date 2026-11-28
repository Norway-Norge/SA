"""Enumerate matching workspace files."""

import fnmatch
import json
import os
import re
import stat
from pathlib import Path, PureWindowsPath

from secureagent.security.workspace import Workspace, is_link_or_reparse
from secureagent.tools.spec import spec

GLOB_WALK_LIMIT = 50_000
GLOB_RESULT_LIMIT = 100

GLOB = spec("glob", "列出本地目录内匹配相对 glob 的普通文件绝对路径，按修改时间从新到旧；最多直接显示 100 项，更多匹配可用 read_spill 查看。扫描可能不完整。",
            {"pattern": {"type": "string", "description": "相对 glob，例如 **/*、*.py；不支持花括号。"},
             "path": {"type": "string", "description": "绝对路径或相对于工作区的起始目录，默认 ."}}, ["pattern"])


def validate_glob_pattern(pattern: str) -> list[str]:
    if (not isinstance(pattern, str) or not pattern or len(pattern) > 1024 or "\x00" in pattern
            or Path(pattern).is_absolute() or PureWindowsPath(pattern).is_absolute()
            or PureWindowsPath(pattern).drive or PureWindowsPath(pattern).root or any(char in pattern for char in "{}")):
        raise ValueError("glob 模式无效：仅支持相对路径，不支持花括号")
    parts = pattern.replace("\\", "/").split("/")
    if any(part == "" for part in parts):
        raise ValueError("glob 模式不能包含空路径段")
    return [part for part in parts if part != "."]


def glob_files(workspace: Workspace, pattern: str, path: str = ".", spill_store=None) -> dict:
    """Enumerate matching regular files under an authorized starting directory."""
    parts = validate_glob_pattern(pattern)
    if not parts:
        raise ValueError("glob 模式不能为空")
    if any(part.casefold() == "node_modules" for part in Path(path).parts):
        raise ValueError("glob 不遍历 node_modules")
    expressions = [None if part == "**" else re.compile(fnmatch.translate(part)) for part in parts]

    def matches(names: tuple[str, ...]) -> bool:
        positions = {0}
        for name in names:
            next_positions = set()
            for index in positions:
                while index < len(expressions) and expressions[index] is None:
                    next_positions.add(index)
                    index += 1
                if index < len(expressions) and expressions[index] is not None and expressions[index].fullmatch(name):
                    next_positions.add(index + 1)
            positions = next_positions
        return any(all(expression is None for expression in expressions[index:]) for index in positions)

    root = workspace.resolve_read(path, directory=True)
    stack = [root]
    found: list[tuple[int, str]] = []
    scanned = 0
    incomplete = False
    while stack:
        directory = stack.pop()
        try:
            # Re-check every directory before opening it; this is not atomic with scandir.
            workspace.resolve_read(str(directory), directory=True).relative_to(root)
            with os.scandir(directory) as entries:
                for entry in entries:
                    if scanned >= GLOB_WALK_LIMIT:
                        incomplete = True
                        break
                    scanned += 1
                    if workspace.exclude_entry(entry.name) or entry.name.casefold() == "node_modules":
                        continue
                    candidate = Path(entry.path)
                    try:
                        if is_link_or_reparse(candidate):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(candidate)
                        elif entry.is_file(follow_symlinks=False) and matches(candidate.relative_to(root).parts):
                            resolved = workspace.resolve_read(str(candidate))
                            resolved.relative_to(root)
                            info = resolved.stat()
                            if stat.S_ISREG(info.st_mode):
                                found.append((info.st_mtime_ns, str(resolved)))
                    except (OSError, ValueError):
                        incomplete = True
            if scanned >= GLOB_WALK_LIMIT:
                break
        except (OSError, ValueError):
            incomplete = True
    found.sort(key=lambda item: (-item[0], item[1]))
    result = {"files": [name for _, name in found[:GLOB_RESULT_LIMIT]], "entries_scanned": scanned,
            "truncated": incomplete or len(found) > GLOB_RESULT_LIMIT,
            "result_limit": GLOB_RESULT_LIMIT, "walk_limit": GLOB_WALK_LIMIT,
            "directories_included": False,
            "excluded": "策略排除项、链接及 node_modules 不列出；目录与空目录不返回"}
    if len(found) > GLOB_RESULT_LIMIT and spill_store is not None:
        full = {**result, "files": [name for _, name in found], "truncated": incomplete,
                "note": "结果只覆盖本次实际扫描；扫描中断或达到上限时仍不完整。"}
        try:
            result["spill_id"] = spill_store.save(json.dumps(full, ensure_ascii=False))
            result["read_hint"] = "使用 read_spill 分页读取本次扫描的全部匹配结果。"
        except (OSError, ValueError, UnicodeError):
            result["spill_error"] = "完整扫描结果无法保存；请缩小搜索范围。"
    return result

