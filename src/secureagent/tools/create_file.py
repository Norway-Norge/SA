"""Create-only file tool; authorization is enforced by the dispatcher."""

from secureagent.tools.spec import PATH, spec

CREATE = spec("create_file", "在工作区内创建新的 UTF-8 文本文件；不覆盖现有文件。缺失的父目录会自动创建。",
              {"path": PATH, "content": {"type": "string", "description": "新文件完整文本。"}},
              ["path", "content"])


from secureagent.security.workspace import Workspace
from secureagent.tools.file_backend import MAX_CREATE_BYTES, create_workspace_file
from secureagent.tools.file_service import FileSystem


def create_file(workspace: Workspace, path: str, content: str, *, file_system: FileSystem | None = None) -> dict:
    data = content.encode("utf-8")
    if len(data) > MAX_CREATE_BYTES:
        return {"error": "文件内容超过大小限制"}
    try:
        return file_system.create_file(path, data) if file_system is not None else create_workspace_file(workspace, path, data)
    except Exception:
        return {"status": "UNKNOWN", "error": "创建结果未知，请先核对文件，不要自动重试"}
