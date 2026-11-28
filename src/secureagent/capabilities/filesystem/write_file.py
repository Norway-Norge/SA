"""Replace an observed existing file using a guarded local commit."""

from secureagent.tools.spec import spec

WRITE = spec("write_file", "写入已有的空 UTF-8 文本文件，范围受当前权限控制。必须在当前会话先完整读取；非空文件待审批功能实现后再开放。",
             {"path": {"type": "string", "description": "绝对路径或相对于工作区的路径；修改范围受权限控制。"}, "content": {"type": "string", "description": "替换后的完整 UTF-8 文本。"},
              "sandbox_permissions": {"type": "string", "description": "需要单次提升时填写 workspace-write 或 danger-full-access。"},
              "justification": {"type": "string", "description": "请求本次权限升级的原因，以及对拟写入内容及其可能后果的简短说明；内容作为文本写入不等于执行。"}},
             ["path", "content"])


from secureagent.security.observations import ObservationStore
from secureagent.security.file_access import RestrictedFileSystem
from secureagent.security.workspace import Workspace
from secureagent.capabilities.filesystem.create_file import MAX_CREATE_BYTES
from secureagent.capabilities.filesystem.file_service import FileSystem, LocalFileSystem


def write_file(workspace: Workspace, observations: ObservationStore, path: str, content: str,
               *, file_system: FileSystem | None = None) -> dict:
    fs = file_system if file_system is not None else RestrictedFileSystem(workspace, LocalFileSystem(workspace.root))
    data = content.encode("utf-8")
    if len(data) > MAX_CREATE_BYTES:
        return {"error": "文件内容超过大小限制"}
    target = fs.resolve(path)
    observation = observations.get(target.key)
    if observation is None:
        return {"error": "请先在当前会话完整读取文件", "code": "NOT_OBSERVED"}
    if not observation.complete:
        return {"error": "文件只被部分读取，请完整读取后再写入", "code": "PARTIAL_READ"}
    if fs.current_version(target) != observation.version:
        observations.invalidate(target.key)
        return {"error": "文件自读取后已变化，请重新读取", "code": "STALE_VERSION"}
    if observation.size != 0:
        return {"error": "非空文件写入需要审批；当前尚未提供审批功能", "code": "REVIEW_REQUIRED"}
    try:
        result = fs.replace_observed(path, target, observation.version, data, "write")
        observations.invalidate(target.key)
    except Exception:
        observations.invalidate(target.key)
        result = {"status": "UNKNOWN", "error": "写入结果未知，请先核对文件，不要自动重试"}
    return result
