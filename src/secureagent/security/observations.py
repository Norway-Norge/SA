"""Per-session read observations; never grants access by itself."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from threading import RLock


_current_session: ContextVar[str | None] = ContextVar("sa_read_session", default=None)


def current_session() -> str | None:
    return _current_session.get()


@contextmanager
def read_session(session_id: str):
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("会话标识无效")
    token = _current_session.set(session_id)
    try:
        yield
    finally:
        _current_session.reset(token)


@dataclass(frozen=True)
class ReadObservation:
    resource: str
    version: object
    size: int
    offset: int
    returned_lines: int
    total_lines: int
    complete: bool


def file_identity(st) -> tuple[int, int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


class ObservationStore:
    """Memory-only: absence after a restart must require another read."""

    def __init__(self):
        self._items: dict[tuple[str, str], ReadObservation] = {}
        self._lock = RLock()

    def record(self, observation: ReadObservation) -> None:
        session = _current_session.get()
        if session is not None:
            with self._lock:
                self._items[(session, observation.resource)] = observation

    def get(self, resource: str) -> ReadObservation | None:
        session = _current_session.get()
        if session is None:
            return None
        with self._lock:
            return self._items.get((session, resource))

    def invalidate(self, resource: str) -> None:
        session = _current_session.get()
        if session is not None:
            with self._lock:
                self._items.pop((session, resource), None)

    def check_overwrite(self, resource: str, current_version: object) -> tuple[bool, str]:
        """Check observed content only; caller must separately enforce authorization.

        `current_version` must come from a trusted backend. This is not an atomic
        write guard and must not be used alone to authorize a write.
        """
        observation = self.get(resource)
        if observation is None:
            return False, "NOT_OBSERVED"
        if not observation.complete:
            return False, "PARTIAL_READ"
        if observation.version != current_version:
            return False, "STALE_VERSION"
        return True, "OBSERVED"
