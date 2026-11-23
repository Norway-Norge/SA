"""Replace an observed existing file using a guarded local commit."""

from secureagent.security.observations import ObservationStore
from secureagent.security.workspace import Workspace
from secureagent.tools.create_file import MAX_CREATE_BYTES
from secureagent.tools.file_service import FileSystem, LocalFileSystem


def write_file(workspace: Workspace, observations: ObservationStore, path: str, content: str,
               *, file_system: FileSystem | None = None) -> dict:
    fs = file_system if file_system is not None else LocalFileSystem(workspace)
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
