"""Codex command-hook bridge plugin."""

from secureagent.plugins.security.hook_bridge import installer as bridge_installer


def installer(path, *, model=""):
    return bridge_installer(path, "codex", model=model)
