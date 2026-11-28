"""Workspace-scoped registry for explicitly trusted MCP server entries."""

from threading import RLock
from urllib.parse import urlsplit
import re

from cordis_py import Service


class McpManagerService(Service):
    """Track explicitly registered servers and their lifecycle-bound clients."""

    def __init__(self, ctx):
        self._lock = RLock()
        self._servers = {}
        # 将管理服务注册到当前工作区的 Cordis 上下文。
        super().__init__(ctx, "mcpManager")

    def register(self, name, config):
        """Register an entry and return its identity-bound removal callback.

        The caller must bind the returned callback to its plugin lifecycle.
        No command is started and no network connection is made.
        """
        return self._register(name, config, "client_not_implemented")

    def register_client(self, name, config, *, client=None):
        """Record a configured client; connection is established only when used."""
        return self._register(name, config, "configured", client)

    def _register(self, name, config, status, client=None):
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name) is None:
            raise ValueError("MCP 服务器名称无效")
        validated = self._validate_config(config)
        entry = {"config": validated, "status": status, "client": client}
        with self._lock:
            if name in self._servers:
                raise ValueError("MCP 服务器名称已注册")
            self._servers[name] = entry

        def dispose():
            with self._lock:
                if self._servers.get(name) is entry:
                    del self._servers[name]
        return dispose

    def get_client(self, name):
        """Return a registered client, or None; caller must use a trusted plugin."""
        with self._lock:
            entry = self._servers.get(name)
            return entry["client"] if entry else None

    @staticmethod
    def _validate_config(config):
        if not isinstance(config, dict):
            raise ValueError("MCP 服务器配置无效")
        transport = config.get("transport")
        if transport == "stdio":
            if set(config) - {"transport", "command", "args"}:
                raise ValueError("MCP 服务器配置包含不支持的字段")
            command, args = config.get("command"), config.get("args", [])
            if (not isinstance(command, str) or not command.strip() or "\x00" in command
                    or not isinstance(args, list)
                    or any(not isinstance(arg, str) or "\x00" in arg for arg in args)):
                raise ValueError("MCP stdio 配置无效")
            return {"transport": transport, "command": command, "args": tuple(args)}
        if transport == "streamable-http":
            if set(config) != {"transport", "url"}:
                raise ValueError("MCP 服务器配置包含不支持或缺少的字段")
            url = config.get("url")
            if not isinstance(url, str) or not url or any(ord(char) <= 32 for char in url):
                raise ValueError("MCP HTTP 地址无效")
            try:
                parsed = urlsplit(url)
                port = parsed.port
            except ValueError as exc:
                raise ValueError("MCP HTTP 地址无效") from exc
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or parsed.fragment or port == 0):
                raise ValueError("MCP HTTP 地址无效")
            return {"transport": transport, "url": url}
        raise ValueError("不支持的 MCP 传输方式")

    def list(self):
        """Return non-sensitive status; registration does not imply connectivity."""
        with self._lock:
            return [{"name": name, "transport": entry["config"]["transport"],
                     "status": entry["status"]}
                    for name, entry in sorted(self._servers.items())]
