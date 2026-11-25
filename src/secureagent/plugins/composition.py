"""Compose one trusted, workspace-scoped SA runtime."""

from pathlib import Path

from cordis_py import Context

from secureagent.security.policy import Policy
from secureagent.security.observations import ObservationStore
from secureagent.security.edit_approvals import EditApprovals
from secureagent.tools.edit_file import decide_edit
from secureagent.tools.registry import ToolRegistry
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



class RuntimeComposition:
    """Own services, providers and plugin installations for one workspace."""

    def __init__(self, policy: Policy, sandbox_image: str | None = None,
                 *, registry: ToolRegistry | None = None, cache_dir: Path | None = None):
        self.policy = policy
        self.observations = ObservationStore()
        self.edit_approvals = EditApprovals()
        self.sandbox_image = sandbox_image
        self.sandboxes = StatefulTestSandboxes(policy.workspace, sandbox_image, cache_dir=cache_dir) if sandbox_image else None
        tool_registry = registry if registry is not None else ToolRegistry()
        self.safe_delete = None
        self.context = Context()
        self.plugin_runtime = TrustedPluginRuntime(tool_registry, policy, context=self.context)
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
            self.plugins.register_tool("read-spill", read_spill.install, dependencies=("spillStore", "systemPrompt"))
            self.plugins.register_tool("skills-provider-local", local_skills.installer(policy.workspace), dependencies=("skills",))
            self.plugins.register_tool("skill", skill.install, dependencies=("skills", "systemPrompt"))
            self.file_system_service = FileSystemService(self.plugin_runtime.context, policy.workspace,
                                                         on_change=self._invalidate_filesystem_state)
            self.recycle.file_system = self.file_system_service
            self.filesystem_plugins = FileSystemComposition(self.plugins, self.file_system_service)
            self.filesystem_plugins.register_factory("fs-provider-local", local_filesystem.installer(policy.workspace))
            self.filesystem_plugins.reload_from_environment()
            self.plugins.register_tool("file-search-provider-local", local_file_search.installer(policy.workspace, self.spills), dependencies=("fileSearch",))
            file_plugins = [
                ("read-file", read_file.installer(policy, self.observations), ("fs", "systemPrompt")),
                ("search-files", search_files.install, ("fileSearch", "systemPrompt")),
                ("glob", glob.install, ("fileSearch", "systemPrompt")),
                ("create-file", create_file.installer(policy), ("fs", "systemPrompt")),
                ("write-file", write_file.installer(policy, self.observations), ("fs", "systemPrompt")),
                ("edit-file", edit_file.installer(policy, self.observations, lambda: self.edit_approvals,
                    lambda: self.policy, lambda decide: setattr(self, "_decide_edit", decide)), ("fs", "systemPrompt")),
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
                self.plugins.register_tool("web-search", web_search.install, dependencies=("web", "systemPrompt"))
            if web_fetch_enabled():
                self.plugins.register_tool("web-fetch-provider-deepseek", deepseek_fetch.install, dependencies=("web",))
                self.plugins.register_tool("web-fetch", web_fetch.install, dependencies=("web", "systemPrompt"))
            self.recycle_backend = SafeDeleteOperations(policy, self.observations)
            self.plugins.register_tool("recycle-provider-local", local_recycle.installer(self.recycle_backend), dependencies=("recycle",))
            self.safe_delete = self.recycle
            recycle_plugins = (("safe-delete", safe_delete), ("list-recycle-bin", list_recycle_bin),
                               ("restore-file", restore_file), ("convert-file", convert_file))
            recycle_disposers = []
            try:
                for plugin_name, module in recycle_plugins:
                    recycle_disposers.append(self.plugins.register_tool(plugin_name, module.installer(self.safe_delete), dependencies=("systemPrompt",)))
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
                self.plugins.register_tool("run-inline-tests", run_inline_tests.install, dependencies=("testRunner", "systemPrompt"))
                self.plugins.register_tool("run-tests", run_tests.install, dependencies=("testRunner", "systemPrompt"))


    def _invalidate_filesystem_state(self):
        self.observations.invalidate_all()
        self.edit_approvals.invalidate_pending()
        recycle_backend = getattr(self, "recycle_backend", None)
        if recycle_backend is not None:
            recycle_backend.discard_pending()


def create_dispatcher(policy: Policy, sandbox_image: str | None = None,
                      *, registry: ToolRegistry | None = None, cache_dir: Path | None = None):
    """Host entry point: assemble services first, then create the dispatcher."""
    from secureagent.tools.dispatcher import Dispatcher

    runtime = RuntimeComposition(policy, sandbox_image, registry=registry, cache_dir=cache_dir)
    return Dispatcher(policy, runtime=runtime)
