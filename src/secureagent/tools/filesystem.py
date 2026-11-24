"""Bounded read-only workspace operations."""

import codecs
import json
import os
import fnmatch
import re
import stat
from pathlib import Path, PureWindowsPath
from typing import Callable

from secureagent.security.workspace import (
    Workspace,
    WorkspaceError,
    is_link_or_reparse,
    is_sensitive_component,
)
from secureagent.security.observations import ReadObservation
from secureagent.security.file_access import RestrictedFileSystem
from secureagent.tools.file_service import FileSystem, LocalFileSystem

MAX_FILE_BYTES = 256 * 1024
MAX_READ_LINES = 2000
MAX_READ_OUTPUT_BYTES = 50 * 1024
MAX_LINE_LENGTH = 2000
LINE_TRUNCATION_MARKER = "…[line truncated]"
MAX_SCAN_FILES = 1000
MAX_MATCHES = 50
MAX_SAVED_MATCHES = 10_000
GLOB_WALK_LIMIT = 50_000
GLOB_RESULT_LIMIT = 100


def validate_glob_pattern(pattern: str) -> list[str]:
    if (not isinstance(pattern, str) or not pattern or len(pattern) > 1024 or "\x00" in pattern
            or Path(pattern).is_absolute() or PureWindowsPath(pattern).is_absolute()
            or PureWindowsPath(pattern).drive or PureWindowsPath(pattern).root or any(char in pattern for char in "{}")):
        raise ValueError("glob 模式无效：仅支持相对路径，不支持花括号")
    parts = pattern.replace("\\", "/").split("/")
    if any(part in ("..", "") for part in parts):
        raise ValueError("glob 模式不能包含上级目录或空路径段")
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

    root = workspace.resolve(path, directory=True)
    stack = [root]
    found: list[tuple[int, str]] = []
    scanned = 0
    incomplete = False
    while stack:
        directory = stack.pop()
        try:
            # Re-check every directory before opening it; this is not atomic with scandir.
            relative_dir = str(directory.relative_to(workspace.root))
            workspace.resolve(relative_dir, directory=True)
            with os.scandir(directory) as entries:
                for entry in entries:
                    if scanned >= GLOB_WALK_LIMIT:
                        incomplete = True
                        break
                    scanned += 1
                    if is_sensitive_component(entry.name) or entry.name.casefold() == "node_modules":
                        continue
                    candidate = Path(entry.path)
                    try:
                        if is_link_or_reparse(candidate):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(candidate)
                        elif entry.is_file(follow_symlinks=False) and matches(candidate.relative_to(root).parts):
                            resolved = workspace.resolve(str(candidate.relative_to(workspace.root)))
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
            "excluded": "敏感路径、链接及 node_modules 不列出；目录与空目录不返回"}
    if len(found) > GLOB_RESULT_LIMIT and spill_store is not None:
        full = {**result, "files": [name for _, name in found], "truncated": incomplete,
                "note": "结果只覆盖本次实际扫描；扫描中断或达到上限时仍不完整。"}
        try:
            result["spill_id"] = spill_store.save(json.dumps(full, ensure_ascii=False))
            result["read_hint"] = "使用 read_spill 分页读取本次扫描的全部匹配结果。"
        except (OSError, ValueError, UnicodeError):
            result["spill_error"] = "完整扫描结果无法保存；请缩小搜索范围。"
    return result


def read_file(workspace: Workspace, path: str, offset: int = 1,
              limit: int = MAX_READ_LINES,
              on_observed: Callable[[ReadObservation], None] | None = None,
              *, file_system: FileSystem | None = None) -> dict:
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 1:
        return {"error": "offset 必须是大于等于 1 的整数"}
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_READ_LINES:
        return {"error": f"limit 必须是 1 到 {MAX_READ_LINES} 之间的整数"}

    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    fs = file_system if file_system is not None else RestrictedFileSystem(workspace, LocalFileSystem(workspace.root))
    lines: list[dict] = []
    output_bytes = 0
    total_lines = 0
    truncated_by_bytes = False
    line_chunks: list[str] = []
    line_length = 0
    line_seen = False
    line_truncated = False
    any_line_truncated = False
    first_selected = offset
    last_selected = offset + limit - 1

    def append_segment(segment: str) -> None:
        nonlocal line_length, line_seen, line_truncated
        if not segment:
            return
        line_seen = True
        if not first_selected <= total_lines + 1 <= last_selected or truncated_by_bytes:
            return
        available = MAX_LINE_LENGTH - line_length
        if available > 0:
            kept = segment[:available]
            line_chunks.append(kept)
            line_length += len(kept)
        if len(segment) > max(available, 0):
            line_truncated = True

    def finish_line() -> None:
        nonlocal total_lines, output_bytes, truncated_by_bytes
        nonlocal line_chunks, line_length, line_seen, line_truncated, any_line_truncated
        total_lines += 1
        if first_selected <= total_lines <= last_selected and not truncated_by_bytes:
            text = "".join(line_chunks)
            if text.endswith("\r"):
                text = text[:-1]
            if line_truncated:
                any_line_truncated = True
                text = text[:MAX_LINE_LENGTH - len(LINE_TRUNCATION_MARKER)] + LINE_TRUNCATION_MARKER
            rendered = f"{total_lines} | {text}"
            line_bytes = len(rendered.encode("utf-8")) + 1
            if output_bytes + line_bytes > MAX_READ_OUTPUT_BYTES:
                truncated_by_bytes = True
            else:
                lines.append({"number": total_lines, "text": text})
                output_bytes += line_bytes
        line_chunks = []
        line_length = 0
        line_seen = False
        line_truncated = False

    def consume(text: str) -> None:
        segments = text.split("\n")
        for segment in segments[:-1]:
            append_segment(segment)
            finish_line()
        append_segment(segments[-1])

    try:
        with fs.open_read(path) as stream:
            for chunk in stream.chunks():
                consume(decoder.decode(chunk))
            consume(decoder.decode(b"", final=True))
            snapshot = stream.finish()
            target = stream.target
    except UnicodeDecodeError:
        return {"error": "文件不是 UTF-8 文本"}

    if line_seen:
        finish_line()

    # A change during the read must not create a usable observation. Comparing
    # metadata is a best-effort check, not an atomic snapshot against all writers.
    if on_observed is not None and snapshot is not None:
        version, size = snapshot
        on_observed(ReadObservation(
            resource=target.key, version=version, size=size, offset=offset,
            returned_lines=len(lines), total_lines=total_lines,
            complete=(offset == 1 and len(lines) == total_lines
                      and not truncated_by_bytes and not any_line_truncated),
        ))

    return {
        "path": path,
        "offset": offset,
        "lines": lines,
        "total_lines": total_lines,
        "content": "\n".join(f"{line['number']} | {line['text']}" for line in lines),
        "truncated_by_bytes": truncated_by_bytes,
    }


def search_files(workspace: Workspace, path: str, query: str, spill_store=None) -> dict:
    root = workspace.resolve(path, directory=True)
    matches: list[dict] = []
    scanned = 0
    truncated = False
    for directory, dirs, files in os.walk(root, followlinks=False):
        base = Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in {".venv", "__pycache__"}
                         and not is_sensitive_component(d)
                         and not is_link_or_reparse(base / d))
        for name in sorted(files):
            if is_sensitive_component(name):
                continue
            if scanned >= MAX_SCAN_FILES or len(matches) >= MAX_SAVED_MATCHES:
                truncated = True
                break
            scanned += 1
            candidate = base / name
            try:
                relative = str(candidate.relative_to(workspace.root))
                target = workspace.resolve(relative)
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
