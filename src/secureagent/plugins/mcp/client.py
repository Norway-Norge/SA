"""Explicitly installed, lifecycle-bound generic stdio MCP client plugin."""

from secureagent.tools.mcp_client import StdioMcpClient


def installer(name, command, args, *, env=None, timeout=45):
    def install(scope, ctx):
        client = StdioMcpClient(command, args, env, timeout=timeout)
        scope.register_cleanup(client.close)
        scope.register_cleanup(ctx.mcpManager.register_client(name, {
            "transport": "stdio", "command": command, "args": list(args),
        }, client=client))
    return install
