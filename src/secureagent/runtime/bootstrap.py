"""Host composition for the default explicitly trusted plugins."""

import os

from secureagent.plugins.observers.tool_monitor import install as install_tool_monitor
from secureagent.plugins.prompts.catgirl import install as install_catgirl
from secureagent.plugins.prompts.demon import install as install_demon
from secureagent.plugins.prompts.detective import install as install_detective
from secureagent.plugins.prompts.base import install as install_base
from secureagent.plugins.security import hooks_claude_code, hooks_codex
from secureagent.plugins.security import sensitive_read_monitor, sensitive_path_guard, path_traversal_guard


def install_default_plugins(plugins):
    # 宿主显式安装默认提示词、监控器和人格插件。
    plugins.register_prompt("base-system", install_base, dependencies=("systemPrompt",))
    plugins.register_tool("tool-monitor", install_tool_monitor, dependencies=("turn_events",))
    plugins.register_tool("sensitive-read-monitor", sensitive_read_monitor.install, dependencies=("tools",))
    # 路径策略插件默认关闭；可信宿主可分别显式启用。
    if os.environ.get("SA_SENSITIVE_PATH_GUARD", "").strip().lower() in {"1", "true", "yes"}:
        plugins.register_tool("sensitive-path-guard", sensitive_path_guard.install, dependencies=("tools", "security"))
    if os.environ.get("SA_PATH_TRAVERSAL_GUARD", "").strip().lower() in {"1", "true", "yes"}:
        plugins.register_tool("path-traversal-guard", path_traversal_guard.install, dependencies=("tools",))
    # 仅显式配置可信路径时安装命令 Hook，不扫描工作区配置。
    claude_path = os.environ.get("SA_CLAUDE_HOOKS_CONFIG")
    codex_path = os.environ.get("SA_CODEX_HOOKS_CONFIG")
    if claude_path:
        plugins.register_tool("hooks-claude-code", hooks_claude_code.installer(claude_path), dependencies=("tools",))
    if codex_path:
        plugins.register_tool("hooks-codex", hooks_codex.installer(codex_path), dependencies=("tools",))
    plugins.register_prompt("persona-catgirl", install_catgirl, dependencies=("personas",))
    plugins.register_prompt("persona-detective", install_detective, dependencies=("personas",))
    plugins.register_prompt("persona-demon", install_demon, dependencies=("personas",))
    selected = os.environ.get("SA_PERSONA", "catgirl")
    plugins.runtime.context.personas.select(None if selected == "none" else selected)
