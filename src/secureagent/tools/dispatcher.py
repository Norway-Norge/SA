"""Validate every model request before trusted policy and execution."""

import json
import logging
from contextlib import nullcontext
from pathlib import Path

from secureagent.security.policy import Policy
from secureagent.security.workspace import WorkspaceError
from secureagent.security.observations import ObservationStore
from secureagent.security.edit_approvals import EditApprovals
from secureagent.tools.glob import validate_glob_pattern
from secureagent.tools.edit_file import decide_edit
from secureagent.tools.file_service import LocalFileSystem
from secureagent.tools.registry import ToolDefinition, ToolRegistry
from secureagent.tools.web_search import enabled as web_search_enabled
from secureagent.tools.web_fetch import enabled as web_fetch_enabled
from secureagent.tools.shell import StatefulTestSandboxes
from secureagent.plugins.tools import (read_file, search_files, glob, create_file, write_file, edit_file,
    safe_delete, list_recycle_bin, restore_file, convert_file, read_spill, skill, web_search, web_fetch,
    run_tests, run_inline_tests)
from secureagent.tools.safe_delete import SafeDeleteOperations
from secureagent.plugins.runtime import TrustedPluginRuntime
from secureagent.plugins.filesystem_service import FileSystemService
from secureagent.plugins.filesystem_composition import FileSystemComposition
from secureagent.plugins.registry import PluginService
from secureagent.plugins.turn_events import TurnEventService
from secureagent.plugins.persona_service import PersonaService
from secureagent.plugins.system_prompt_service import SystemPromptService
from secureagent.plugins.web_service import WebService
from secureagent.plugins.skill_service import SkillService
from secureagent.plugins.spill_service import SpillService
from secureagent.plugins.test_runner_service import TestRunnerService
from secureagent.plugins.file_search_service import FileSearchService
from secureagent.plugins.recycle_service import RecycleService
from secureagent.plugins.providers import deepseek_search, deepseek_fetch, local_skills, local_spill, local_filesystem, local_file_search, local_recycle, docker_tests
from secureagent.plugins.bootstrap import install_default_plugins

log = logging.getLogger("secureagent.audit")
FILESYSTEM_BOUND_TOOLS = frozenset({"read_file", "create_file", "write_file", "edit_file",
                                    "safe_delete", "restore_file", "convert_file"})
RECYCLE_TOOLS = frozenset({"safe_delete", "restore_file", "convert_file"})


class Dispatcher:
    def __init__(self, policy: Policy, sandbox_image: str | None = None,
                 *, registry: ToolRegistry | None = None, cache_dir: Path | None = None):
        self.policy = policy
        self.observations = ObservationStore()
        self.edit_approvals = EditApprovals()
        self.sandbox_image = sandbox_image
        self.sandboxes = StatefulTestSandboxes(policy.workspace, sandbox_image, cache_dir=cache_dir) if sandbox_image else None
        tool_registry = registry if registry is not None else ToolRegistry()
        self.safe_delete = None
        self.plugin_runtime = TrustedPluginRuntime(tool_registry, policy)
        self.registry = self.plugin_runtime.registry
        self.turn_events = TurnEventService(self.plugin_runtime.context)
        self.personas = PersonaService(self.plugin_runtime.context)
        self.system_prompt = SystemPromptService(self.plugin_runtime.context)
        self.web = WebService(self.plugin_runtime.context)
        self.skills = SkillService(self.plugin_runtime.context)
        self.spills = SpillService(self.plugin_runtime.context)
        self.test_runner = TestRunnerService(self.plugin_runtime.context)
        self.file_search = FileSearchService(self.plugin_runtime.context)
        self.recycle = RecycleService(self.plugin_runtime.context)
        self.system_prompt.section("persona", lambda _: (self.personas.current() or (None, ""))[1], order=1000)
        self.plugins = PluginService(self.plugin_runtime)
        install_default_plugins(self.plugins)
        self.unload_safe_delete = None
        self.unload_file_tools = None
        self._decide_edit = lambda approval_id, approve: decide_edit(
            self.policy, self.observations, self.edit_approvals, approval_id, approve)
        if registry is None:
            self.plugins.register_tool("spill-provider-local", local_spill.installer(policy.workspace), dependencies=("spillStore",))
            self.plugins.register_tool("read-spill", read_spill.install, dependencies=("spillStore",))
            self.plugins.register_tool("skills-provider-local", local_skills.installer(policy.workspace), dependencies=("skills",))
            self.plugins.register_tool("skill", skill.install, dependencies=("skills",))
            self.file_system_service = FileSystemService(self.plugin_runtime.context, policy.workspace,
                                                         on_change=self._invalidate_filesystem_state)
            self.recycle.file_system = self.file_system_service
            self.filesystem_plugins = FileSystemComposition(self.plugins, self.file_system_service)
            self.filesystem_plugins.register_factory("fs-provider-local", local_filesystem.installer(policy.workspace))
            self.filesystem_plugins.reload_from_environment()
            self.plugins.register_tool("file-search-provider-local", local_file_search.installer(policy.workspace, self.spills), dependencies=("fileSearch",))
            file_plugins = [
                ("read-file", read_file.installer(policy, self.observations), ("fs", "systemPrompt")),
                ("search-files", search_files.install, ("fileSearch",)),
                ("glob", glob.install, ("fileSearch",)),
                ("create-file", create_file.installer(policy), ("fs",)),
                ("write-file", write_file.installer(policy, self.observations), ("fs",)),
                ("edit-file", edit_file.installer(policy, self.observations, lambda: self.edit_approvals,
                    lambda: self.policy, lambda decide: setattr(self, "_decide_edit", decide)), ("fs",)),
            ]
            file_disposers = []
            try:
                for plugin_name, install, dependencies in file_plugins:
                    file_disposers.append(self.plugins.register_tool(plugin_name, install, dependencies=dependencies))
            except BaseException:
                for dispose in reversed(file_disposers):
                    dispose()
                raise
            def unload_file_tools():
                for dispose in reversed(file_disposers):
                    dispose()
            self.unload_file_tools = unload_file_tools
            if web_search_enabled():
                self.plugins.register_tool("web-search-provider-deepseek", deepseek_search.install, dependencies=("web",))
                self.plugins.register_tool("web-search", web_search.install, dependencies=("web",))
            if web_fetch_enabled():
                self.plugins.register_tool("web-fetch-provider-deepseek", deepseek_fetch.install, dependencies=("web",))
                self.plugins.register_tool("web-fetch", web_fetch.install, dependencies=("web",))
            self.recycle_backend = SafeDeleteOperations(policy, self.observations)
            self.plugins.register_tool("recycle-provider-local", local_recycle.installer(self.recycle_backend), dependencies=("recycle",))
            self.safe_delete = self.recycle
            recycle_plugins = (("safe-delete", safe_delete), ("list-recycle-bin", list_recycle_bin),
                               ("restore-file", restore_file), ("convert-file", convert_file))
            recycle_disposers = []
            try:
                for plugin_name, module in recycle_plugins:
                    recycle_disposers.append(self.plugins.register_tool(plugin_name, module.installer(self.safe_delete)))
            except BaseException:
                for dispose in reversed(recycle_disposers):
                    dispose()
                raise
            def unload_safe_delete():
                for dispose in reversed(recycle_disposers):
                    dispose()
            self.unload_safe_delete = unload_safe_delete
            if sandbox_image:
                self.plugins.register_tool("tests-provider-docker", docker_tests.installer(
                    policy.workspace, sandbox_image, self.sandboxes), dependencies=("testRunner",))
                self.plugins.register_tool("run-inline-tests", run_inline_tests.install, dependencies=("testRunner",))
                self.plugins.register_tool("run-tests", run_tests.install, dependencies=("testRunner",))

    @property
    def schemas(self) -> list[dict]:
        """Return current registrations rather than the startup snapshot."""
        return self.registry.schemas

    def tool_snapshot(self) -> dict[str, ToolDefinition]:
        return self.registry.snapshot()

    def skill_catalog(self) -> dict:
        return self.skills.list()

    def _invalidate_filesystem_state(self):
        self.observations.invalidate_all()
        self.edit_approvals.invalidate_pending()
        recycle_backend = getattr(self, "recycle_backend", None)
        if recycle_backend is not None:
            recycle_backend.discard_pending()

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
