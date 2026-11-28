"""Session-scoped storage for complete, oversized tool results."""

from secureagent.tools.spec import spec

READ_SPILL = spec("read_spill", "分页读取当前会话保留的完整工具结果，offset 从 0 开始。",
                  {"spill_id": {"type": "string", "description": "工具结果返回的 spill_id。"},
                   "offset": {"type": "integer", "minimum": 0, "description": "起始字符偏移，默认 0。"},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 6000, "description": "返回字符数，默认 6000；转义后的输出过大时会进一步缩小。"}}, ["spill_id"])


import hashlib
import json
import os
import re
import stat
import uuid
from pathlib import Path

from secureagent.security.observations import current_session
from secureagent.security.workspace import Workspace, is_link_or_reparse

THRESHOLD = 8192
PAGE_CHARS = 6000
SPILL_ID = re.compile(r"[0-9a-f]{32}\Z")


class SpillStore:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def _directory(self, *, create: bool) -> Path:
        session = current_session()
        if session is None:
            raise ValueError("当前没有会话")
        digest = hashlib.sha256(session.encode("utf-8")).hexdigest()
        current = self.workspace.root
        for part in (".secureagent", "spill", digest):
            current = current / part
            if create:
                try:
                    current.mkdir(mode=0o700)
                except FileExistsError:
                    pass
            if is_link_or_reparse(current) or not current.is_dir():
                raise ValueError("结果存储目录不可用")
            current.resolve(strict=True).relative_to(self.workspace.root)
        return current

    def save(self, text: str) -> str:
        directory = self._directory(create=True)
        identifier = uuid.uuid4().hex
        path = directory / (identifier + ".txt")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                stream.write(text)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return identifier

    def read(self, identifier: str, offset: int = 0, limit: int = PAGE_CHARS) -> dict:
        if not isinstance(identifier, str) or not SPILL_ID.fullmatch(identifier):
            return {"error": "结果标识无效"}
        if (isinstance(offset, bool) or not isinstance(offset, int) or offset < 0 or
                isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= PAGE_CHARS):
            return {"error": "读取范围无效"}
        try:
            directory = self._directory(create=False)
            path = directory / (identifier + ".txt")
            if is_link_or_reparse(path) or not stat.S_ISREG(path.stat().st_mode):
                return {"error": "结果不存在或不可读取"}
            with path.open("r", encoding="utf-8", newline="") as stream:
                remaining = offset
                while remaining:
                    chunk = stream.read(min(remaining, 8192))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                if remaining:
                    return {"error": "读取偏移超出结果长度"}
                page = stream.read(limit + 1)
            content = page[:limit]
            result = {"content": content, "offset": offset, "next_offset": offset + len(content),
                      "has_more": len(page) > limit}
            if len(json.dumps(result, ensure_ascii=False)) <= THRESHOLD:
                return result
            low, high = 0, len(content)
            while low < high:
                middle = (low + high + 1) // 2
                candidate = {"content": content[:middle], "offset": offset,
                             "next_offset": offset + middle, "has_more": True}
                if len(json.dumps(candidate, ensure_ascii=False)) <= THRESHOLD:
                    low = middle
                else:
                    high = middle - 1
            return {"content": content[:low], "offset": offset, "next_offset": offset + low, "has_more": True}
        except (OSError, ValueError, UnicodeError):
            return {"error": "结果不存在或当前会话无权读取"}

    def retain(self, tool_name: str, result: dict) -> dict:
        text = result.get("content")
        if tool_name in {"read_spill", "web_fetch"} or not isinstance(text, str) or len(text) <= THRESHOLD:
            return result
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            return result
        # Approval and state-bearing results must remain intact for the UI and
        # the model; error semantics and container test output also stay intact.
        if (not isinstance(parsed, dict) or "status" in parsed or "approval_id" in parsed
                or "error" in parsed or "exit_code" in parsed):
            return result
        try:
            identifier = self.save(text)
        except (OSError, ValueError, UnicodeError):
            parsed["spill_warning"] = "完整结果无法保存到会话存储，原始结果仍直接返回。"
            return {**result, "content": json.dumps(parsed, ensure_ascii=False)}
        head, tail = text[:4096], text[-1024:]
        while True:
            content = json.dumps({"preview": head + "\n…（中间省略）…\n" + tail,
                                  "spill_id": identifier, "total_chars": len(text),
                                  "read_hint": "使用 read_spill 按字符偏移分页读取当前会话中的完整工具结果。"}, ensure_ascii=False)
            if len(content) <= THRESHOLD:
                return {**result, "content": content}
            head, tail = head[:len(head) // 2], tail[len(tail) // 2:]
