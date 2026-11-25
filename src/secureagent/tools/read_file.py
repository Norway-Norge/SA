"""Read a workspace text file with bounded output."""

import codecs
from typing import Callable

from secureagent.security.workspace import Workspace
from secureagent.security.observations import ReadObservation
from secureagent.security.file_access import RestrictedFileSystem
from secureagent.tools.file_service import FileSystem, LocalFileSystem
from secureagent.tools.spec import PATH, spec

MAX_READ_LINES = 2000
MAX_READ_OUTPUT_BYTES = 50 * 1024
MAX_LINE_LENGTH = 2000
LINE_TRUNCATION_MARKER = "…[line truncated]"

READ = spec("read_file", "按行读取授权工作区中的 UTF-8 文本文件；使用 offset 和 limit 分页。",
            {"path": PATH,
             "offset": {"type": "integer", "minimum": 1, "description": "起始行号，从 1 开始，默认 1。"},
             "limit": {"type": "integer", "minimum": 1, "maximum": 2000,
                       "description": "最多返回行数，默认 2000。"}}, ["path"])


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

