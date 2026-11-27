"""Publish immutable, observe-only tool result notifications via Cordis."""

import json
import logging

from secureagent.security.pre_execute import ToolInvocation, freeze

log = logging.getLogger("secureagent.events")


def emit_result(ctx, policy, call, message):
    """Emit after result finalization without affecting the completed operation."""
    try:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments", "{}"))
        except (TypeError, ValueError):
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        snapshot = policy.resolve()
        invocation = ToolInvocation(name=str(function.get("name", "")), arguments=freeze(arguments),
            tool_call_id=str(call.get("id", "")), session_id=snapshot.session_id,
            workspace=str(snapshot.workspace_root), permission_mode=snapshot.mode)
        # Copy and freeze before publishing; observers cannot change the result.
        result = freeze(json.loads(json.dumps(message, ensure_ascii=False, allow_nan=False)))
        ctx.emit("tools/result", invocation, result)
    except Exception:
        # Do not log raw arguments or results; notification cannot undo execution.
        log.warning("工具结果事件通知失败")
