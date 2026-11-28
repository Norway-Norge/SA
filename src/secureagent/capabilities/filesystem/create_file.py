"""Create-only file tool; authorization is enforced by the dispatcher."""

from secureagent.tools.spec import spec

CREATE = spec("create_file", "创建新的 UTF-8 文本文件；不覆盖现有文件。缺失的父目录会自动创建，范围受当前权限控制。",
              {"path": {"type": "string", "description": "绝对路径或相对于工作区的路径；修改范围受权限控制。"}, "content": {"type": "string", "description": "新文件完整文本。"},
               "sandbox_permissions": {"type": "string", "description": "需要单次提升时填写 workspace-write 或 danger-full-access。"},
               "justification": {"type": "string", "description": "请求本次权限升级的原因，以及对拟写入内容及其可能后果的简短说明；内容作为文本写入不等于执行。"}},
              ["path", "content"])


from secureagent.security.workspace import Workspace
from secureagent.capabilities.filesystem.file_backend import MAX_CREATE_BYTES, create_workspace_file
from secureagent.capabilities.filesystem.file_service import FileSystem


def create_file(workspace: Workspace, path: str, content: str, *, file_system: FileSystem | None = None) -> dict:
    data = content.encode("utf-8")
    if len(data) > MAX_CREATE_BYTES:
        return {"error": "文件内容超过大小限制"}
    try:
        return file_system.create_file(path, data) if file_system is not None else create_workspace_file(workspace, path, data)
    except Exception:
        return {"status": "UNKNOWN", "error": "创建结果未知，请先核对文件，不要自动重试"}
