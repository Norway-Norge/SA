"""Validate every model request before trusted policy and execution."""

import json
import logging
from contextlib import nullcontext
from pathlib import Path

from secureagent.security.policy import Policy
from secureagent.security.workspace import WorkspaceError
from secureagent.tools.glob import validate_glob_pattern
from secureagent.tools.file_service import LocalFileSystem
from secureagent.tools.registry import ToolDefinition, ToolRegistry

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
            from secureagent.plugins.composition import RuntimeComposition
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

    def skill_catalog(self) -> dict:
        return self.skills.list()

    def decide_edit(self, approval_id: str, approve: bool) -> dict:
        fs = getattr(self, "file_system_service", None)
        try:
            with fs.operation() if fs is not None else nullcontext():
                return self._decide_edit(approval_id, approve)
        except WorkspaceError:
            return {"error": "文件系统 provider 不可用", "code": "FS_PROVIDER_UNAVAILABLE"}

    def execute(self, call: dict, *, expected: ToolDefinition | None = None, check_expected: bool = False) -> dict:
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
            allowed, reason = self.policy.authorize(name, args)
            log.info("call_id=%r tool=%r decision=%s", str(call_id)[:100], str(name)[:100],
                     "ALLOW" if allowed else "DENY")
            if not allowed:
                result = {"error": reason}
            else:
                fs = getattr(self, "file_system_service", None)
                with fs.operation() if fs is not None and name in FILESYSTEM_BOUND_TOOLS else nullcontext() as backend:
                    if name in RECYCLE_TOOLS and fs is not None and not isinstance(backend.provider, LocalFileSystem):
                        raise WorkspaceError("当前文件系统 provider 不支持本地回收操作")
                    result = tool.handler(args)
        except (ValueError, TypeError, OSError) as exc:
            log.info("call_id=%r tool=%r decision=DENY", str(call_id)[:100], str(name)[:100])
            result = {"error": "工具请求无效或执行失败", "detail": str(exc) if isinstance(exc, ValueError) else "操作失败"}
        return {"role": "tool", "tool_call_id": call_id,
                "content": json.dumps(result, ensure_ascii=False)}
