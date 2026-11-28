"""Local skill discovery and loading provider."""

from pathlib import Path

from secureagent.capabilities.skills.discovery import discover_skills
from secureagent.capabilities.skills.skill import load_skill


def installer(workspace):
    def install(scope, ctx):
        class LocalSkills:
            def list(self):
                return discover_skills(workspace, user_home=Path.home())

            def load(self, name):
                return load_skill(workspace, name, user_home=Path.home())

        provider = LocalSkills()
        provider.list = scope.guard(provider.list)
        provider.load = scope.guard(provider.load)
        # 注册能力提供方，并在插件卸载时撤销注册。
        scope.register_cleanup(ctx.skills.register_provider("local", provider))
    return install
