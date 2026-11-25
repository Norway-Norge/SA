"""Literal edits are proposed first, then committed after a bound approval."""

from secureagent.tools.spec import PATH, spec

EDIT = spec("edit_file", "在已完整读取的文件中精确替换文字。提交后必须等待用户审批。默认要求原文只匹配一次。",
            {"path": PATH, "old_string": {"type": "string"}, "new_string": {"type": "string"},
             "replace_all": {"type": "boolean"}}, ["path", "old_string", "new_string"])


from secureagent.security.edit_approvals import EditApprovals, PendingEdit
from secureagent.security.file_access import RestrictedFileSystem
from secureagent.security.observations import ObservationStore
from secureagent.security.policy import Policy
from secureagent.tools.create_file import MAX_CREATE_BYTES
from secureagent.tools.file_service import FileSystem, LocalFileSystem, observed_bytes as _observed_bytes


def _normalized(value: str) -> str:
    return value.replace("\r\n", "\n")


def _edit(raw: bytes, old: str, new: str, replace_all: bool) -> tuple[bytes | None, str | None]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "文件不是 UTF-8 文本"
    if "\x00" in text:
        return None, "不能编辑二进制文件"
    crlf_count = text.count("\r\n")
    lf_count = text.count("\n") - crlf_count
    if crlf_count and lf_count:
        return None, "文件包含混合行尾，暂不支持自动编辑"
    norm, old, new = _normalized(text), _normalized(old), _normalized(new)
    if not old or old == new:
        return None, "old_string 不能为空且必须不同于 new_string"
    count = norm.count(old)
    if count == 0:
        return None, "未找到 old_string"
    if count > 1 and not replace_all:
        return None, "old_string 匹配多处；请提供更具体的文本或指定 replace_all"
    modified = norm.replace(old, new, -1 if replace_all else 1)
    if crlf_count:
        modified = modified.replace("\n", "\r\n")
    data = modified.encode("utf-8")
    if len(data) > MAX_CREATE_BYTES:
        return None, "编辑结果超过大小限制"
    return data, None


def propose_edit(policy: Policy, observations: ObservationStore, approvals: EditApprovals,
                 path: str, old_string: str, new_string: str, replace_all: bool = False,
                 *, file_system: FileSystem | None = None) -> dict:
    fs = file_system if file_system is not None else RestrictedFileSystem(
        policy.workspace, LocalFileSystem(policy.workspace.root))
    allowed, reason = policy.authorize("edit_file", {"path": path})
    if not allowed:
        return {"code": "DENIED", "error": reason}
    if len(old_string.encode("utf-8")) > MAX_CREATE_BYTES or len(new_string.encode("utf-8")) > MAX_CREATE_BYTES:
        return {"error": "编辑参数超过大小限制"}
    target = fs.resolve(path)
    observation = observations.get(target.key)
    if observation is None:
        return {"code": "NOT_OBSERVED", "error": "请先在当前会话完整读取文件"}
    if not observation.complete:
        return {"code": "PARTIAL_READ", "error": "请先完整读取文件"}
    if fs.current_version(target) != observation.version:
        observations.invalidate(target.key)
        return {"code": "STALE_VERSION", "error": "文件已变化，请重新读取"}
    raw = fs.read_observed(target, observation.version)
    if raw is None:
        observations.invalidate(target.key)
        return {"code": "STALE_VERSION", "error": "文件已变化，请重新读取"}
    data, error = _edit(raw, old_string, new_string, replace_all)
    if error:
        return {"code": "EDIT_INVALID", "error": error}
    # Preview reading must still refer to the observed file.
    if fs.current_version(target) != observation.version:
        observations.invalidate(target.key)
        return {"code": "STALE_VERSION", "error": "文件已变化，请重新读取"}
    approval_id = approvals.propose(path, old_string, new_string, replace_all, observation)
    if not approval_id:
        return {"error": "缺少会话，无法申请审批"}
    return {"status": "REVIEW", "approval_id": approval_id, "path": path,
            "message": "编辑尚未执行，等待用户审批"}


def decide_edit(policy: Policy, observations: ObservationStore, approvals: EditApprovals,
                approval_id: str, approve: bool, *, file_system: FileSystem | None = None) -> dict:
    fs = file_system if file_system is not None else RestrictedFileSystem(
        policy.workspace, LocalFileSystem(policy.workspace.root))
    pending = approvals.take(approval_id)
    if pending is None:
        return {"error": "审批不存在、已处理、已过期或不属于当前会话", "code": "INVALID_APPROVAL"}
    try:
        if not approve:
            result = {"status": "DENIED", "message": "用户拒绝编辑"}
        else:
            allowed, reason = policy.authorize("edit_file", {"path": pending.path})
            result = _commit(policy, observations, pending, fs) if allowed else {"error": reason, "code": "DENIED"}
    except Exception:
        observations.invalidate(pending.observation.resource)
        result = {"error": "审批后无法完成编辑，请检查文件状态", "status": "UNKNOWN"}
    approvals.finish(approval_id, result)
    return result


def _commit(policy: Policy, observations: ObservationStore, pending: PendingEdit, fs: FileSystem) -> dict:
    target = fs.resolve(pending.path)
    observation = pending.observation
    if target.key != observation.resource or fs.current_version(target) != observation.version:
        observations.invalidate(observation.resource)
        return {"code": "STALE_VERSION", "error": "文件已变化，请重新读取并申请审批"}
    raw = fs.read_observed(target, observation.version)
    if raw is None:
        observations.invalidate(observation.resource)
        return {"code": "STALE_VERSION", "error": "文件已变化，请重新读取并申请审批"}
    data, error = _edit(raw, pending.old_string, pending.new_string, pending.replace_all)
    if error:
        return {"code": "EDIT_INVALID", "error": error}
    result = fs.replace_observed(pending.path, target, observation.version, data, "edit",
                                 lambda: policy.authorize("edit_file", {"path": pending.path}))
    observations.invalidate(observation.resource)
    return result
