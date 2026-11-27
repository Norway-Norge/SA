"""Claude Code command-hook bridge plugin."""

from secureagent.plugins.security.hook_bridge import installer as bridge_installer


def installer(path):
    return bridge_installer(path, "claude")
