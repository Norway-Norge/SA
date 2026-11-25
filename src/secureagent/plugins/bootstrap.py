"""Host composition for the default explicitly trusted plugins."""

import os

from secureagent.plugins.observers.tool_monitor import install as install_tool_monitor
from secureagent.plugins.prompts.catgirl import install as install_catgirl
from secureagent.plugins.prompts.demon import install as install_demon
from secureagent.plugins.prompts.detective import install as install_detective
from secureagent.plugins.prompts.base import install as install_base


def install_default_plugins(plugins):
    # 宿主显式安装默认提示词、监控器和人格插件。
    plugins.register_prompt("base-system", install_base, dependencies=("systemPrompt",))
    plugins.register_tool("tool-monitor", install_tool_monitor, dependencies=("turn_events",))
    plugins.register_prompt("persona-catgirl", install_catgirl, dependencies=("personas",))
    plugins.register_prompt("persona-detective", install_detective, dependencies=("personas",))
    plugins.register_prompt("persona-demon", install_demon, dependencies=("personas",))
    selected = os.environ.get("SA_PERSONA", "catgirl")
    plugins.runtime.context.personas.select(None if selected == "none" else selected)
