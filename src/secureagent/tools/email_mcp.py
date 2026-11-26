"""Narrow adapter for the community email-send stdio MCP server."""

import asyncio
import json
import socket
from pathlib import Path

from secureagent.tools.mcp_client import StdioMcpClient, list_session_tools


class EmailMcpClient:
    def __init__(self, node, server, smtp_host, smtp_port, smtp_user, smtp_password, sender, *, mcp_client=None):
        self.node, self.server = node, str(Path(server))
        self.smtp_host, self.smtp_port = smtp_host, smtp_port
        self.smtp_user, self._smtp_password, self.sender = smtp_user, smtp_password, sender
        self.mcp_client = mcp_client or StdioMcpClient(self.node, [self.server], {
            "SMTP_HOST": smtp_host, "SMTP_PORT": str(smtp_port), "SMTP_SECURE": "true",
            "SMTP_USER": smtp_user, "SMTP_PASS": smtp_password,
        })

    def send(self, recipient, subject, body):
        # A single operation is never automatically retried after a possible send.
        try:
            return asyncio.run(asyncio.wait_for(self._send(recipient, subject, body), timeout=45))
        except Exception:
            return {"status": "UNKNOWN", "message": "邮件发送状态无法确认，请检查发件箱后再决定是否重试。"}

    async def _send(self, recipient, subject, body):
        try:
            # Resolve with the operating system and retain the hostname for TLS verification.
            addresses = await asyncio.to_thread(socket.getaddrinfo, self.smtp_host, self.smtp_port,
                                                type=socket.SOCK_STREAM)
            if not addresses:
                return {"status": "UNKNOWN", "message": "邮件服务器地址解析失败；邮件未发送。"}
            address = addresses[0][4][0]

            async def send_with_session(session):
                tools = await list_session_tools(session)
                send_tool = next((tool for tool in tools if tool.name == "send-email"), None)
                properties = send_tool.inputSchema.get("properties", {}) if send_tool else {}
                if "tlsServername" not in properties:
                    return {"status": "UNKNOWN", "message": "邮件服务版本不支持安全的系统 DNS 连接；邮件未发送。"}
                result = await session.call_tool("send-email", {
                    "transport": "smtp", "host": address, "port": self.smtp_port,
                    "secure": True, "tlsRejectUnauthorized": True, "authUser": self.smtp_user,
                    "tlsServername": self.smtp_host,
                    "from": self.sender, "to": recipient, "subject": subject, "text": body,
                })
                if result.isError or len(result.content) != 1 or result.content[0].type != "text":
                    return {"status": "UNKNOWN", "message": "邮件发送状态无法确认，请检查发件箱。"}
                payload = json.loads(result.content[0].text)
                accepted, rejected = payload.get("accepted"), payload.get("rejected")
                if (payload.get("ok") is True and isinstance(accepted, list)
                        and isinstance(rejected, list) and recipient.casefold() in
                        [item.casefold() for item in accepted if isinstance(item, str)] and not rejected):
                    return {"status": "SENT", "message": "邮件服务器已接受邮件；最终投递状态未知。"}
                return {"status": "UNKNOWN", "message": "邮件发送状态无法确认，请检查发件箱。"}

            async with asyncio.timeout(45):
                return await self.mcp_client.execute(send_with_session)
        except Exception:
            # A timeout, lost reply or MCP error can occur after SMTP acceptance.
            return {"status": "UNKNOWN", "message": "邮件发送状态无法确认，请检查发件箱后再决定是否重试。"}
