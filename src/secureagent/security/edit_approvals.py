"""Single-use, session-bound approvals for exact proposed edits."""

import secrets
import threading
import time
from dataclasses import dataclass

from secureagent.security.observations import ReadObservation, current_session


@dataclass(frozen=True)
class PendingEdit:
    session_id: str
    path: str
    old_string: str
    new_string: str
    replace_all: bool
    observation: ReadObservation
    expires_at: float


class EditApprovals:
    def __init__(self, ttl_seconds: int = 600):
        self._ttl = ttl_seconds
        self._lock = threading.RLock()
        self._pending: dict[str, PendingEdit] = {}
        self._results: dict[str, tuple[str, dict, float]] = {}

    def propose(self, path: str, old: str, new: str, replace_all: bool,
                observation: ReadObservation) -> str | None:
        session = current_session()
        if not session:
            return None
        now = time.monotonic()
        with self._lock:
            self._pending = {key: item for key, item in self._pending.items() if item.expires_at > now}
            self._results = {key: item for key, item in self._results.items() if item[2] > now}
            if len(self._pending) >= 256:
                raise ValueError("待审批请求过多")
            approval_id = secrets.token_urlsafe(24)
            self._pending[approval_id] = PendingEdit(session, path, old, new, replace_all,
                                                     observation, now + self._ttl)
            return approval_id

    def take(self, approval_id: str) -> PendingEdit | None:
        session = current_session()
        with self._lock:
            pending = self._pending.get(approval_id)
            if pending is None or pending.session_id != session:
                return None
            del self._pending[approval_id]
            if pending.expires_at <= time.monotonic():
                return None
            self._results[approval_id] = (session, {"status": "PROCESSING"}, time.monotonic() + self._ttl)
            return pending

    def finish(self, approval_id: str, result: dict) -> None:
        with self._lock:
            previous = self._results.get(approval_id)
            if previous and previous[0] == current_session() and previous[1].get("status") == "PROCESSING":
                self._results[approval_id] = (previous[0], dict(result), previous[2])

    def status(self, approval_id: str) -> dict:
        session = current_session()
        now = time.monotonic()
        with self._lock:
            pending = self._pending.get(approval_id)
            if pending and pending.session_id == session and pending.expires_at > now:
                return {"status": "REVIEW"}
            result = self._results.get(approval_id)
            if result and result[0] == session and result[2] > now:
                return dict(result[1])
        return {"status": "UNKNOWN", "message": "审批状态不可用，请先检查文件，再决定是否重新申请。"}
