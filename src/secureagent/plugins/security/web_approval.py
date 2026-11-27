"""Per-stream, single-use approval bridge for web pre-execute policies."""

import json
import secrets
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

from secureagent.plugins.security.cli_approval import _plain

MAX_DETAILS_CHARS = 20000
DEFAULT_TIMEOUT_SECONDS = 120
_delivery = ContextVar("sa_web_approval_delivery", default=None)


@contextmanager
def approval_delivery(delivery):
    token = _delivery.set(delivery)
    try:
        yield
    finally:
        _delivery.reset(token)


class WebApprovalRegistry:
    def __init__(self):
        self._lock = threading.RLock()
        self._pending = {}

    def open(self, send, *, timeout=DEFAULT_TIMEOUT_SECONDS):
        return WebApprovalDelivery(self, send, timeout)

    def decide(self, approval_id, session_id, approve):
        with self._lock:
            item = self._pending.get(approval_id)
            if (item is None or item["session_id"] != session_id or item["delivery"].closed
                    or time.monotonic() >= item["deadline"]):
                return False
            del self._pending[approval_id]
            item["approved"] = approve is True
            item["event"].set()
            return True


class WebApprovalDelivery:
    def __init__(self, registry, send, timeout):
        self.registry, self.send, self.timeout = registry, send, timeout
        self.closed = False

    def request(self, call, reason):
        if not call.session_id or not call.tool_call_id:
            return False
        try:
            details = {"tool": call.name, "tool_call_id": call.tool_call_id,
                       "workspace": call.workspace, "permission_mode": call.permission_mode,
                       "arguments": _plain(call.arguments), "reason": reason}
            if len(json.dumps(details, ensure_ascii=False, allow_nan=False)) > MAX_DETAILS_CHARS:
                return False
        except (TypeError, ValueError):
            return False
        approval_id = secrets.token_urlsafe(24)
        item = {"session_id": call.session_id, "delivery": self, "approved": False,
                "event": threading.Event(), "deadline": time.monotonic() + self.timeout}
        with self.registry._lock:
            if self.closed or len(self.registry._pending) >= 256:
                return False
            self.registry._pending[approval_id] = item
        try:
            self.send({"type": "approval_request", "approval_id": approval_id,
                       "session_id": call.session_id, **details})
            item["event"].wait(self.timeout)
            with self.registry._lock:
                self.registry._pending.pop(approval_id, None)
                approved = item["approved"] and not self.closed
            self.send({"type": "approval_closed", "approval_id": approval_id})
            return approved
        except Exception:
            with self.registry._lock:
                self.registry._pending.pop(approval_id, None)
            return False

    def close(self):
        with self.registry._lock:
            self.closed = True
            for key, item in list(self.registry._pending.items()):
                if item["delivery"] is self:
                    del self.registry._pending[key]
                    item["approved"] = False
                    item["event"].set()


def install(scope, ctx):
    def approve(call, reason):
        delivery = _delivery.get()
        return False if delivery is None else delivery.request(call, reason)
    scope.register_pre_execute_approval(approve)
