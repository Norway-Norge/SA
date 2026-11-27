"""Interactive, session-bound approval for pre-execute policy decisions."""

import json
import sys
from collections.abc import Mapping

from secureagent.security.observations import current_session


MAX_DISPLAY_CHARS = 20000


def _plain(value):
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def installer(session_id, *, input_fn=None, output_fn=None, is_interactive=None):
    """Install a handler owned by one CLI session; no decision is retained."""
    def install(scope, ctx):
        def approve(call, reason):
            read = input_fn or input
            write = output_fn or print
            interactive = is_interactive or (lambda: sys.stdin.isatty())
            if (not interactive() or not session_id or current_session() != session_id
                    or call.session_id != session_id or not call.tool_call_id):
                return False
            try:
                # JSON escaping prevents terminal control sequences from executing.
                details = json.dumps({"tool": call.name, "tool_call_id": call.tool_call_id,
                    "workspace": call.workspace, "permission_mode": call.permission_mode,
                    "arguments": _plain(call.arguments), "reason": reason},
                    ensure_ascii=True, allow_nan=False, indent=2)
                if len(details) > MAX_DISPLAY_CHARS:
                    write("审批信息过长，无法完整展示；本次调用已拒绝。")
                    return False
                write("通用工具审批：请核对本次工具、工作区、原因和完整参数：")
                write(details)
                return read("仅允许本次调用？输入 yes 批准，其他输入拒绝：").strip().lower() == "yes"
            except (EOFError, KeyboardInterrupt, OSError, TypeError, ValueError):
                return False
        scope.register_pre_execute_approval(approve)
    return install
