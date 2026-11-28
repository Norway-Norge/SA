"""Trusted, single-user P0 authorization boundary."""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from .workspace import Workspace, WorkspaceError
from .observations import current_session


SANDBOX_MODES = frozenset({"read-only", "workspace-write", "danger-full-access"})
HOST_WRITE_TOOLS = frozenset({"create_file", "write_file", "edit_file"})
_CALL_MODE = ContextVar("sa_call_mode", default=None)


@dataclass(frozen=True)
class SandboxExecutionPolicy:
    mode: str
    workspace_root: Path
    session_id: str | None


class Policy:
    def __init__(self, workspace: Workspace, mode: str = "workspace-write"):
        if mode not in SANDBOX_MODES:
            raise ValueError("SA_SANDBOX_MODE 无效")
        self.workspace = workspace
        self._mode = mode
        self._lock = RLock()
        self._revision = 0
        self._write_tools = set(HOST_WRITE_TOOLS)
        self._rule_owners: dict[str, object] = {}
        self._rules: dict[str, Callable[[dict], object]] = {
            "read_file": lambda args: self.workspace.resolve_read(args["path"]),
            "search_files": lambda args: self.workspace.resolve_read(args["path"], directory=True),
            "web_search": lambda args: None,
            "web_fetch": lambda args: None,
            "skill": lambda args: None,
            "read_spill": lambda args: None,
            "glob": lambda args: self.workspace.resolve_read(args.get("path", "."), directory=True),
            "run_tests": lambda args: self.workspace.resolve(args["path"], directory=True),
            "run_inline_tests": lambda args: None,
            "create_file": lambda args: self._resolve_create(args["path"]),
            "write_file": lambda args: self._resolve_write(args["path"]),
            "edit_file": lambda args: self._resolve_write(args["path"]),
        }

    def _resolve_create(self, path):
        if self.resolve().mode == "danger-full-access":
            return self.workspace.resolve_new_file_unrestricted(path)
        return self.workspace.resolve_new_file(path, allow_missing_parents=True)

    def _resolve_write(self, path):
        if self.resolve().mode == "danger-full-access":
            return self.workspace.resolve_read(path)
        return self.workspace.resolve(path)

    def register_rule(self, name: str, check: Callable[[dict], object], *, writes: bool = False) -> Callable[[], None]:
        if not isinstance(name, str) or not name:
            raise ValueError("授权规则名称无效或已存在")
        if not callable(check):
            raise TypeError("授权规则必须可调用")
        owner = object()
        with self._lock:
            if name in self._rules:
                raise ValueError("授权规则名称无效或已存在")
            self._rules[name] = check
            self._rule_owners[name] = owner
            self._revision += 1
            if writes:
                self._write_tools.add(name)

        def dispose() -> None:
            with self._lock:
                if self._rule_owners.get(name) is owner:
                    del self._rules[name]
                    del self._rule_owners[name]
                    self._revision += 1
                    if writes:
                        self._write_tools.discard(name)

        return dispose

    @property
    def revision(self):
        with self._lock:
            return self._revision

    def resolve(self) -> SandboxExecutionPolicy:
        """Resolve a trusted per-call snapshot; model arguments cannot change it."""
        grant = _CALL_MODE.get()
        mode = grant[1] if grant is not None and grant[0] is self else self._mode
        return SandboxExecutionPolicy(mode, self.workspace.root, current_session())

    @contextmanager
    def call_mode(self, mode):
        if mode not in SANDBOX_MODES:
            raise ValueError("权限模式无效")
        token = _CALL_MODE.set((self, mode))
        try:
            yield
        finally:
            _CALL_MODE.reset(token)

    def authorize(self, name: str, args: dict) -> tuple[bool, str]:
        execution_policy = self.resolve()
        with self._lock:
            check = self._rules.get(name)
            is_write = name in self._write_tools
        if check is None:
            return False, "工具未授权"
        if execution_policy.mode == "read-only" and is_write:
            return False, "只读模式不允许修改宿主工作区"
        try:
            if check(args) is False:
                return False, "授权规则拒绝"
        except WorkspaceError as exc:
            if name == "create_file" and str(exc) == "目标已存在":
                return False, "目标已存在；请先读取，然后使用 write_file 修改"
            if (execution_policy.mode == "workspace-write" and name in HOST_WRITE_TOOLS
                    and isinstance(args.get("path"), str)):
                try:
                    candidate = (self.workspace.root / Path(args["path"])).resolve(strict=False)
                except (OSError, RuntimeError, ValueError):
                    pass
                else:
                    try:
                        candidate.relative_to(self.workspace.root)
                    except ValueError:
                        return False, ("目标位于工作区外；如用户要求修改，可使用同一工具并设置 "
                                       "sandbox_permissions=danger-full-access 和 justification 请求本次审批。")
            return False, "资源不存在或不在授权工作区内"
        except KeyError:
            return False, "资源不存在或不在授权工作区内"
        return True, "允许"
