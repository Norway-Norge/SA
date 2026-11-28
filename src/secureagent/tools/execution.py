"""Around-dispatch waterfall for an already authorized tool invocation."""

import json

from secureagent.security.pre_execute import run_coroutine_sync


def execute_tool(ctx, invocation, tool, arguments, still_authorized):
    """Execute once through listeners; tool identity and arguments stay fixed."""
    # Listeners receive only the frozen invocation, never the handler's mutable args.
    encoded_args = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
    entered = False

    async def dispatch(current):
        nonlocal entered
        if current is not invocation or entered:
            raise ValueError("工具调用身份已变化或重复执行")
        entered = True
        if not still_authorized():
            return {"error": "工具或权限策略已变化，请重新发起调用"}
        return tool.handler(json.loads(encoded_args))

    async def waterfall():
        return await ctx.waterfall("tools/execute", invocation, fallback=dispatch)

    try:
        result = run_coroutine_sync(waterfall)
        # Detach results from middleware-owned objects and verify JSON compatibility.
        result = json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))
        if not isinstance(result, dict):
            raise ValueError("工具执行结果必须是对象")
        return result
    except Exception:
        # A wrapper may fail after the handler changed state; never retry here.
        return {"status": "UNKNOWN", "error": "工具执行状态未知，请先核对，不要自动重试。"}
