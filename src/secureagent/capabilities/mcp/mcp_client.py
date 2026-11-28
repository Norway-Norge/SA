"""Reusable one-shot stdio MCP client for trusted SA plugins."""

import asyncio
import os
import threading
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def list_session_tools(session):
    """Read every tools/list page, rejecting repeated cursors and excessive pages."""
    tools, seen, cursor = [], set(), None
    for _ in range(100):
        page = await session.list_tools(cursor=cursor) if cursor else await session.list_tools()
        tools.extend(page.tools)
        cursor = getattr(page, "nextCursor", None)
        if not cursor:
            return tools
        if cursor in seen:
            raise ValueError("MCP tools/list returned a repeated cursor")
        seen.add(cursor)
    raise ValueError("MCP tools/list exceeded the page limit")


class StdioMcpClient:
    """Start a server per call; never retry a potentially side-effecting call."""

    def __init__(self, command, args, env=None, *, timeout=45):
        self._params = StdioServerParameters(command=command, args=list(args), env=dict(env or {}))
        self._timeout = timeout
        self._condition = threading.Condition()
        self._active = 0
        self._active_threads = {}
        self._closed = False

    async def execute(self, callback):
        thread = threading.get_ident()
        with self._condition:
            if self._closed:
                raise RuntimeError("MCP client is closed")
            self._active += 1
            self._active_threads[thread] = self._active_threads.get(thread, 0) + 1
        try:
            async with asyncio.timeout(self._timeout):
                # Server diagnostics may contain credentials; do not copy them to logs.
                with open(os.devnull, "w", encoding="utf-8") as errlog:
                    async with stdio_client(self._params, errlog=errlog) as (read, write):
                        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session:
                            await session.initialize()
                            return await callback(session)
        finally:
            with self._condition:
                self._active -= 1
                self._active_threads[thread] -= 1
                if not self._active_threads[thread]:
                    del self._active_threads[thread]
                self._condition.notify_all()

    def list_tools(self):
        return asyncio.run(self.execute(list_session_tools))

    def call_tool(self, name, arguments):
        async def call(session):
            return await session.call_tool(name, arguments)
        return asyncio.run(self.execute(call))

    def close(self):
        with self._condition:
            if threading.get_ident() in self._active_threads:
                raise RuntimeError("Cannot close an MCP client from its active call")
            self._closed = True
            while self._active:
                self._condition.wait()
