"""Session-bound approval and model tool for the community email MCP server."""

import re
import secrets
import sqlite3
import threading
import time

from secureagent.security.observations import current_session
from secureagent.security.email_evidence import lookup_tool_message
from secureagent.tools.spec import spec

SEND = spec("send_email", "准备发送一封纯文本邮件，须经用户审批后才发送。",
            {"to": {"type": "string"}, "subject": {"type": "string"},
             "body": {"type": "string"}}, ["to", "subject", "body"])


class EmailApprovals:
    def __init__(self, client, get_policy, operations, *, authorize=None):
        self.client, self.get_policy, self.operations = client, get_policy, operations
        self.authorize = authorize or (lambda name, args: self.get_policy().authorize(name, args))
        self._lock = threading.RLock()
        self._pending, self._results = {}, {}
        self._closed = False

    def propose(self, args):
        session = current_session()
        if not session:
            return {"error": "缺少会话，无法申请发信审批"}
        recipient, subject, body = args["to"], args["subject"], args["body"]
        if (not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+", recipient)
                or len(recipient) > 254 or len(subject) > 200 or any(c in subject for c in "\r\n\x00")
                or not body.strip() or "\x00" in body or len(body.encode("utf-8")) > 20000):
            return {"error": "邮件地址、主题或正文无效"}
        parameter_hash = self.operations.parameter_hash(self.client, args)
        with self._lock:
            if self._closed:
                return {"error": "邮件插件已卸载"}
            now = time.monotonic()
            self._pending = {k: v for k, v in self._pending.items() if v[2] > now}
            self._results = {k: v for k, v in self._results.items() if v[2] > now}
            if len(self._pending) >= 256:
                return {"error": "待审批请求过多"}
            try:
                operation_id, state, fresh = self.operations.prepare(session, parameter_hash)
            except (OSError, sqlite3.Error, ValueError):
                return {"status": "UNKNOWN", "error": "无法读取邮件操作记录，未创建发送审批。"}
            if state != "PREPARED":
                return self.check(operation_id)
            for existing_id, item in self._pending.items():
                if item[0] == session and item[3] == operation_id:
                    return {"status": "REVIEW", "approval_id": existing_id, "operation_id": operation_id,
                            "from": self.client.sender, "to": recipient, "subject": subject, "body": body,
                            "provider_query": "UNSUPPORTED", "tool_message": lookup_tool_message(operation_id),
                            "message": "相同邮件已有待审批请求，未重复创建。"}
            if not fresh:
                return self.check(operation_id)
            approval_id = secrets.token_urlsafe(24)
            self._pending[approval_id] = (session, dict(args), now + 600, operation_id, parameter_hash)
        return {"status": "REVIEW", "approval_id": approval_id, "operation_id": operation_id, "from": self.client.sender,
                "to": recipient, "subject": subject, "body": body, "message": "邮件尚未发送，等待用户审批"}

    def decide(self, approval_id, approve):
        session = current_session()
        with self._lock:
            item = self._pending.get(approval_id)
            if not item or item[0] != session or item[2] <= time.monotonic() or self._closed:
                return {"code": "INVALID_APPROVAL", "error": "审批不存在、已处理、已过期或不属于当前会话"}
            del self._pending[approval_id]
            self._results[approval_id] = (session, {"status": "PROCESSING"}, time.monotonic() + 600)
        if not approve:
            result = {"status": "DENIED", "message": "用户拒绝发送邮件"}
        else:
            try:
                allowed, _ = self.authorize("send_email", item[1])
            except Exception:
                allowed = False
            if not allowed:
                result = {"status": "DENIED", "message": "发信授权已失效"}
            else:
                try:
                    if not self.operations.begin_send(item[3], session, item[4]):
                        result = self.check(item[3])
                    else:
                        result = self.client.send(item[1]["to"], item[1]["subject"], item[1]["body"])
                        if result.get("status") not in {"SENT", "UNKNOWN"}:
                            result = {"status": "UNKNOWN", "message": "邮件发送状态无法确认，请检查发件箱。"}
                        self.operations.finish(item[3], session, item[4], result["status"], result)
                except Exception:
                    result = {"status": "UNKNOWN", "message": "邮件发送状态无法确认，请检查发件箱。"}
        if result.get("status") == "DENIED":
            try:
                self.operations.finish(item[3], session, item[4], "DENIED", result)
            except (OSError, sqlite3.Error, ValueError):
                result = {"status": "UNKNOWN", "message": "无法确认操作记录，请检查发件箱。"}
        result = {**result, "operation_id": item[3]}
        with self._lock:
            self._results[approval_id] = (session, result, time.monotonic() + 600)
        return result

    def status(self, approval_id):
        session = current_session()
        with self._lock:
            item = self._pending.get(approval_id)
            if item and item[0] == session and item[2] > time.monotonic():
                return {"status": "REVIEW"}
            result = self._results.get(approval_id)
            if result and result[0] == session and result[2] > time.monotonic():
                return dict(result[1])
        return {"status": "UNKNOWN", "message": "审批状态不可用，请检查发件箱。"}

    def check(self, operation_id):
        session = current_session()
        if not session or not isinstance(operation_id, str) or len(operation_id) > 64:
            return {"error": "操作标识无效"}
        try:
            operation = self.operations.get(operation_id, session)
        except (OSError, sqlite3.Error, ValueError):
            return {"status": "UNKNOWN", "operation_id": operation_id, "message": "无法读取操作记录，禁止自动重试。"}
        if operation is None:
            return {"error": "操作不存在或不属于当前会话"}
        operation["provider_query"] = "UNSUPPORTED"
        operation["tool_message"] = lookup_tool_message(operation_id)
        operation["message"] = ("SMTP 服务不支持按操作 ID 查询；工具消息仅代表审批请求，"
                                "不能证明已发送。请核对服务商发件箱，禁止自动重试。")
        return operation

    def close(self):
        with self._lock:
            self._closed = True
            self._pending.clear()


def installer(approvals, dispose_client, bind_decision):
    def install(scope, ctx):
        scope.register_cleanup(dispose_client)
        scope.register_cleanup(approvals.close)
        bind_decision(scope.guard(approvals.decide))
        scope.register(SEND, approvals.propose)
        scope.register_cleanup(ctx.get("systemPrompt").section("tool:send_email",
            "send_email 仅提交邮件审批；结果未知或遇到重复操作时调用 check_email_operation 核查，不要自动重试。",
            order=100, required_tool="send_email"))
    return install
