"""Validate every model request before trusted policy and execution."""

import json
import logging
from pathlib import Path

from secureagent.security.policy import Policy
from secureagent.security.observations import ObservationStore, current_session
from secureagent.security.edit_approvals import EditApprovals
from secureagent.tools.filesystem import validate_glob_pattern
from secureagent.tools.edit_file import decide_edit
from secureagent.tools.file_tools import register_file_read_tools, register_file_glob_tool, register_file_write_tools
from secureagent.tools.file_service import LocalFileSystem
from secureagent.tools.registry import ToolDefinition, ToolRegistry
from secureagent.tools.web_search import DeepSeekSearchProvider, enabled as web_search_enabled, web_search
from secureagent.tools.web_fetch import DeepSeekFetchProvider, enabled as web_fetch_enabled, web_fetch
from secureagent.skills import discover_skills, load_skill
from secureagent.tools.spill import SpillStore
from secureagent.tools.shell import run_tests, StatefulTestSandboxes
from secureagent.tools.inline_tests import run_inline_tests
from secureagent.plugins.safe_delete import SafeDeletePlugin
from secureagent.plugins.runtime import TrustedPluginRuntime
from secureagent.tools.spec import WEB_SEARCH, WEB_FETCH, SKILL, READ_SPILL, TESTS, INLINE_TESTS

log = logging.getLogger("secureagent.audit")


class Dispatcher:
    def __init__(self, policy: Policy, sandbox_image: str | None = None,
                 *, registry: ToolRegistry | None = None, cache_dir: Path | None = None):
        self.policy = policy
        self.observations = ObservationStore()
        self.edit_approvals = EditApprovals()
        self.sandbox_image = sandbox_image
        self.sandboxes = StatefulTestSandboxes(policy.workspace, sandbox_image, cache_dir=cache_dir) if sandbox_image else None
        self.registry = registry or ToolRegistry()
        self.spills = SpillStore(policy.workspace)
        self.safe_delete = None
        self.plugin_runtime = TrustedPluginRuntime(self.registry, policy)
        self.unload_safe_delete = None
        self.unload_file_tools = None
        self._decide_edit = lambda approval_id, approve: decide_edit(
            self.policy, self.observations, self.edit_approvals, approval_id, approve)
        if registry is None:
            self.registry.register(READ_SPILL, lambda args: self.spills.read(args["spill_id"], args.get("offset", 0), args.get("limit", 6000)))
            self.registry.register(SKILL, lambda args: load_skill(policy.workspace, args["name"], user_home=Path.home()))
            self.plugin_runtime.context.register("fs", LocalFileSystem(policy.workspace))
            def register_files(scope, ctx):
                file_system = ctx.fs
                self._decide_edit = scope.guard(lambda approval_id, approve: decide_edit(
                    self.policy, self.observations, self.edit_approvals, approval_id, approve, file_system=file_system))
                register_file_read_tools(scope, policy, self.observations, self.spills, file_system)
                register_file_glob_tool(scope, policy, self.spills)
                register_file_write_tools(scope, policy, self.observations, lambda: self.edit_approvals, file_system)
            self.unload_file_tools = self.plugin_runtime.mount(register_files, dependencies=("fs",), with_context=True)
            if web_search_enabled():
                provider = DeepSeekSearchProvider()
                self.registry.register(WEB_SEARCH, lambda args: web_search(args["query"], provider))
            if web_fetch_enabled():
                fetch_provider = DeepSeekFetchProvider()
                self.registry.register(WEB_FETCH, lambda args: web_fetch(args["url"], fetch_provider))
            self.safe_delete = SafeDeletePlugin(policy, self.observations)
            def register_safe_delete(scope):
                self.safe_delete.decide = scope.guard(self.safe_delete.decide)
                self.safe_delete.register(scope, scope)
            self.unload_safe_delete = self.plugin_runtime.mount(register_safe_delete)
            if sandbox_image:
                self.registry.register(INLINE_TESTS, lambda args: run_inline_tests(
                    args["code"], args["tests"], sandbox_image))
                self.registry.register(TESTS, lambda args: run_tests(
                    policy.workspace, args["path"], args["runner"], sandbox_image) if current_session() is None else
                    self.sandboxes.run(current_session(), args["path"], args["runner"]))

    @property
    def schemas(self) -> list[dict]:
        """Return current registrations rather than the startup snapshot."""
        return self.registry.schemas

    def tool_snapshot(self) -> dict[str, ToolDefinition]:
        return self.registry.snapshot()

    def skill_catalog(self) -> dict:
        return discover_skills(self.policy.workspace, user_home=Path.home())

    def decide_edit(self, approval_id: str, approve: bool) -> dict:
        return self._decide_edit(approval_id, approve)

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
                result = tool.handler(args)
        except (ValueError, TypeError, OSError) as exc:
            log.info("call_id=%r tool=%r decision=DENY", str(call_id)[:100], str(name)[:100])
            result = {"error": "工具请求无效或执行失败", "detail": str(exc) if isinstance(exc, ValueError) else "操作失败"}
        return {"role": "tool", "tool_call_id": call_id,
                "content": json.dumps(result, ensure_ascii=False)}
