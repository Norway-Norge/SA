"""Validate every model request before trusted policy and execution."""

import json
import logging
from contextlib import nullcontext
from pathlib import Path

from secureagent.security.policy import Policy
from secureagent.security.workspace import WorkspaceError
from secureagent.security.pre_execute import DecisionKind, ToolInvocation, freeze
from secureagent.capabilities.filesystem.glob import validate_glob_pattern
from secureagent.capabilities.filesystem.file_service import LocalFileSystem
from secureagent.tools.registry import ToolDefinition, ToolRegistry
from secureagent.runtime.tool_result_event import emit_result

log = logging.getLogger("secureagent.audit")
FILESYSTEM_BOUND_TOOLS = frozenset({"read_file", "create_file", "write_file", "edit_file",
                                    "safe_delete", "restore_file", "convert_file"})
RECYCLE_TOOLS = frozenset({"safe_delete", "restore_file", "convert_file"})


class Dispatcher:
    def __init__(self, policy: Policy, sandbox_image: str | None = None,
                 *, registry: ToolRegistry | None = None, cache_dir: Path | None = None,
                 runtime=None):
        if runtime is None:
            # Compatibility for existing callers; application entry points compose explicitly.
            from secureagent.runtime.composition import RuntimeComposition
            runtime = RuntimeComposition(policy, sandbox_image, registry=registry, cache_dir=cache_dir)
        elif runtime.policy is not policy or sandbox_image is not None or registry is not None or cache_dir is not None:
            raise ValueError("注入的运行时与 Dispatcher 参数不一致")
        self.runtime = runtime

    def __getattr__(self, name):
        return getattr(self.runtime, name)

    @property
    def policy(self):
        return self.runtime.policy

    @policy.setter
    def policy(self, value):
        self.runtime.policy = value

    @property
    def edit_approvals(self):
        return self.runtime.edit_approvals

    @edit_approvals.setter
    def edit_approvals(self, value):
        self.runtime.edit_approvals = value

    @property
    def schemas(self) -> list[dict]:
        """Return current registrations rather than the startup snapshot."""
        return self.registry.schemas

    def tool_snapshot(self) -> dict[str, ToolDefinition]:
        return self.registry.snapshot()

    def emit_tool_result(self, call: dict, message: dict) -> None:
        emit_result(self.plugin_runtime.context, self.policy, call, message)

    def skill_catalog(self) -> dict:
        return self.skills.list()

    def decide_edit(self, approval_id: str, approve: bool) -> dict:
        fs = getattr(self, "file_system_service", None)
        try:
            with fs.operation() if fs is not None else nullcontext():
                return self._decide_edit(approval_id, approve)
        except WorkspaceError:
            return {"error": "文件系统 provider 不可用", "code": "FS_PROVIDER_UNAVAILABLE"}

    def execute(self, call: dict, *, expected: ToolDefinition | None = None, check_expected: bool = False,
                defer_result_event: bool = False) -> dict:
        """Finalize every tool result before it reaches spill, logs, or the model."""
        call_id = call.get("id", "")
        function = call.get("function") or {}
        name = function.get("name", "")
        try:
            message = self._execute(call, expected=expected, check_expected=check_expected)
            result = json.loads(message["content"])
        except Exception:
            # The handler may already have committed a side effect.
            result = {"status": "UNKNOWN", "error": "工具执行状态未知，请先核对，不要自动重试。"}
        try:
            args = json.loads(function.get("arguments", "{}"))
            if not isinstance(args, dict):
                args = {}
        except (TypeError, ValueError):
            args = {}
        snapshot = self.policy.resolve()
        invocation = ToolInvocation(name=str(name), arguments=freeze(args), tool_call_id=str(call_id),
            session_id=snapshot.session_id, workspace=str(snapshot.workspace_root), permission_mode=snapshot.mode)
        final_result = self.registry.post_execute(invocation, result)
        if name == "read_file" and final_result != result:
            # A filtered or replaced read must not count as a complete observation.
            try:
                self.observations.invalidate(self.file_system_service.resolve(args["path"]).key)
            except Exception:
                self.observations.invalidate_all()
        result = final_result
        message = {"role": "tool", "tool_call_id": call_id,
                   "content": json.dumps(result, ensure_ascii=False)}
        if not defer_result_event:
            self.emit_tool_result(call, message)
        return message

    def _execute(self, call: dict, *, expected: ToolDefinition | None = None, check_expected: bool = False) -> dict:
        call_id = call.get("id", "")
        function = call.get("function") or {}
        name = function.get("name", "")
        try:
            args = json.loads(function.get("arguments", ""))
            if not isinstance(args, dict):
                raise ValueError("参数必须是对象")
            tool = self.registry.get(name)
            if tool is None or (name in {"run_tests", "run_inline_tests"} and not self.sandbox_image):
                raise ValueError("工具未注册")
            if check_expected and (expected is None or tool is not expected):
                raise ValueError("工具未在当前模型请求中提供或注册已变化")
            parameters = tool.schema["function"]["parameters"]
            properties = parameters.get("properties", {})
            required = set(parameters.get("required", []))
            if not required.issubset(args) or not set(args).issubset(properties):
                raise ValueError("参数不符合工具 Schema")
            for key, value in args.items():
                parameter = properties[key]
                parameter_type = parameter.get("type")
                valid = (parameter_type == "string" and isinstance(value, str)
                         or parameter_type == "integer" and isinstance(value, int) and not isinstance(value, bool)
                         or parameter_type == "number" and isinstance(value, (int, float))
                         and not isinstance(value, bool)
                         or parameter_type == "boolean" and isinstance(value, bool))
                if not valid:
                    raise ValueError("参数不符合工具 Schema")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if "minimum" in parameter and value < parameter["minimum"]:
                        raise ValueError("参数不符合工具 Schema")
                    if "maximum" in parameter and value > parameter["maximum"]:
                        raise ValueError("参数不符合工具 Schema")
            if "path" in args and (not args["path"] or len(args["path"]) > 1024):
                raise ValueError("路径参数无效")
            if name == "search_files" and (not args["query"] or len(args["query"]) > 200):
                raise ValueError("搜索词无效")
            if name == "glob":
                validate_glob_pattern(args["pattern"])
            if name == "run_tests" and args["runner"] not in {"pytest", "unittest"}:
                raise ValueError("测试类型无效")
            policy_owner = self.policy
            policy_snapshot = policy_owner.resolve()
            policy_revision = policy_owner.revision
            guard_revision = self.registry.guard_revision
            invocation = ToolInvocation(
                name=name, arguments=freeze(args), tool_call_id=str(call_id),
                session_id=policy_snapshot.session_id, workspace=str(policy_snapshot.workspace_root),
                permission_mode=policy_snapshot.mode)
            requested_mode = args.get("sandbox_permissions") if name in {"create_file", "write_file", "edit_file"} else None
            justification = args.get("justification", "") if name in {"create_file", "write_file", "edit_file"} else ""
            ranks = {"read-only": 0, "workspace-write": 1, "danger-full-access": 2}
            if requested_mode is None:
                if justification.strip():
                    raise ValueError("权限说明需要同时指定权限模式")
                upgraded = False
            else:
                if requested_mode not in ranks or ranks[requested_mode] < ranks[policy_snapshot.mode]:
                    raise ValueError("请求的权限模式无效或低于当前模式")
                upgraded = ranks[requested_mode] > ranks[policy_snapshot.mode]
                if upgraded and not justification.strip():
                    raise ValueError("提升权限需要说明原因")
            reason = (f"请求本次操作从 {policy_snapshot.mode} 升级到 {requested_mode}：{justification.strip()}"
                      if upgraded else None)
            if name == "pwsh":
                reason = "PowerShell 将以 SA 宿主进程权限执行；当前文件工具的权限模式不限制该进程。请审查本次命令。"
            decision = self.registry.pre_execute(invocation, required_approval=reason)
            if decision.kind != DecisionKind.ALLOW:
                result = {"error": decision.reason or ("工具调用已取消" if decision.kind == DecisionKind.CANCEL else "前置策略拒绝"),
                          "code": "TOOL_CANCELLED" if decision.kind == DecisionKind.CANCEL else "TOOL_DENIED"}
                log.info("call_id=%r tool=%r decision=%s", str(call_id)[:100], str(name)[:100], decision.kind.value)
                return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)}
            # A policy or approval must not authorize a removed or replaced tool.
            if self.registry.get(name) is not tool:
                raise ValueError("工具注册已变化")
            if (self.policy is not policy_owner or policy_owner.resolve() != policy_snapshot
                    or policy_owner.revision != policy_revision or self.registry.guard_revision != guard_revision):
                raise ValueError("权限策略已变化，请重新发起调用")
            with policy_owner.call_mode(requested_mode) if upgraded else nullcontext():
                allowed, reason = self.registry.check_guards(invocation)
                log.info("call_id=%r tool=%r decision=%s", str(call_id)[:100], str(name)[:100],
                         "ALLOW" if allowed else "DENY")
                if not allowed:
                    result = {"error": reason}
                else:
                    fs = getattr(self, "file_system_service", None)
                    with fs.operation() if fs is not None and name in FILESYSTEM_BOUND_TOOLS else nullcontext() as backend:
                        if name in RECYCLE_TOOLS and fs is not None and not isinstance(backend.provider, LocalFileSystem):
                            raise WorkspaceError("当前文件系统 provider 不支持本地回收操作")
                        active_snapshot = policy_owner.resolve()
                        def still_authorized():
                            return (self.registry.get(name) is tool and self.policy is policy_owner
                                    and policy_owner.resolve() == active_snapshot
                                    and policy_owner.revision == policy_revision
                                    and self.registry.guard_revision == guard_revision)
                        result = self.registry.execute(invocation, tool, args, still_authorized)
        except (ValueError, TypeError, OSError) as exc:
            log.info("call_id=%r tool=%r decision=DENY", str(call_id)[:100], str(name)[:100])
            result = {"error": "工具请求无效或执行失败", "detail": str(exc) if isinstance(exc, ValueError) else "操作失败"}
        return {"role": "tool", "tool_call_id": call_id,
                "content": json.dumps(result, ensure_ascii=False)}
