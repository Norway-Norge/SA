"""Shared, explicitly configured PreToolUse command-hook compatibility."""

import asyncio
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from secureagent.security.pre_execute import DecisionKind, PreExecuteDecision

_MAX_CONFIG = 262144
_MAX_OUTPUT = 65536


def _plain(value):
    from collections.abc import Mapping
    if isinstance(value, Mapping):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [_plain(v) for v in value]
    return value


def load_config(path):
    raw = Path(path).read_bytes()
    if len(raw) > _MAX_CONFIG:
        raise ValueError("Hook 配置过大")
    config = json.loads(raw.decode("utf-8"))
    if not isinstance(config, dict):
        raise ValueError("Hook 配置必须是对象")
    if "hooks" in config:
        config = config["hooks"]
    if not isinstance(config, dict) or set(config) - {"PreToolUse"}:
        raise ValueError("当前桥接只支持 PreToolUse 事件")
    groups = config.get("PreToolUse", [])
    if not isinstance(groups, list):
        raise ValueError("PreToolUse 必须是数组")
    compiled = []
    for group in groups:
        if not isinstance(group, dict) or set(group) - {"matcher", "hooks"}:
            raise ValueError("无效 Hook 匹配组")
        matcher = group.get("matcher") or ".*"
        if not isinstance(matcher, str):
            raise ValueError("Hook matcher 必须是字符串")
        pattern = re.compile(matcher)
        hooks = group.get("hooks")
        if not isinstance(hooks, list) or not hooks:
            raise ValueError("Hook 列表不能为空")
        for hook in hooks:
            if not isinstance(hook, dict) or set(hook) - {"type", "command", "timeout"}:
                raise ValueError("不支持的 Hook 配置")
            command = hook.get("command")
            timeout = hook.get("timeout", 60)
            if (hook.get("type") != "command" or not isinstance(command, str) or not command.strip()
                    or isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600):
                raise ValueError("仅支持有超时的同步 command Hook")
            compiled.append((pattern, command, timeout))
    return tuple(compiled)


def run_hook(command, timeout, invocation, dialect, model):
    args = _plain(invocation.arguments)
    payload = {"session_id": invocation.session_id or "", "cwd": invocation.workspace,
               "hook_event_name": "PreToolUse", "tool_name": invocation.name}
    if dialect == "claude":
        payload.update(transcript_path="", tool_input=args, tool_use_id=invocation.tool_call_id)
    else:
        payload.update(transcript_path=None, tool_input={"command": args.get("command", "")},
                       model=model, permission_mode="default")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if dialect == "claude":
        data += b"\n"
    env = os.environ.copy()
    if dialect == "claude":
        env["CLAUDE_PROJECT_DIR"] = invocation.workspace
    # Hook 命令来自明确指定的可信配置；输出先落入临时文件并限制读取量。
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        result = subprocess.run(command, shell=True, cwd=invocation.workspace, input=data,
                                stdout=stdout, stderr=stderr, timeout=timeout, env=env)
        stdout.seek(0)
        stderr.seek(0)
        output = stdout.read(_MAX_OUTPUT + 1)
        errors = stderr.read(_MAX_OUTPUT + 1)
    if len(output) > _MAX_OUTPUT or len(errors) > _MAX_OUTPUT:
        raise ValueError("Hook 输出超限")
    if result.returncode == 2:
        return DecisionKind.DENY, errors.decode("utf-8", "replace").strip()[:1000] or "Hook 拒绝了工具调用"
    if result.returncode != 0:
        raise ValueError("Hook 执行失败")
    if not output.strip():
        return DecisionKind.ALLOW, ""
    try:
        parsed = json.loads(output.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError("Hook 输出不是有效 JSON") from None
    if not isinstance(parsed, dict):
        raise ValueError("Hook 输出必须是对象")
    if parsed.get("continue") is False:
        raise ValueError("不支持 Hook 中止整个运行")
    if dialect == "claude":
        details = parsed.get("hookSpecificOutput", {})
        if not isinstance(details, dict) or "updatedInput" in details:
            raise ValueError("不支持 Hook 修改输入")
        decision = details.get("permissionDecision")
        reason = details.get("permissionDecisionReason", "")
        if decision not in (None, "allow", "deny", "ask"):
            raise ValueError("不支持的 Hook 决策")
        kind = {"deny": DecisionKind.DENY, "ask": DecisionKind.ASK}.get(decision, DecisionKind.ALLOW)
    else:
        if "updatedInput" in parsed or "hookSpecificOutput" in parsed:
            raise ValueError("不支持 Hook 修改输入或此输出格式")
        decision = parsed.get("decision")
        if decision not in (None, "block"):
            raise ValueError("不支持的 Hook 决策")
        kind = DecisionKind.DENY if decision == "block" else DecisionKind.ALLOW
        reason = parsed.get("reason", "")
    if not isinstance(reason, str):
        raise ValueError("Hook 原因无效")
    return kind, reason[:1000]


def installer(path, dialect, *, model=""):
    if dialect not in ("claude", "codex"):
        raise ValueError("未知 Hook 类型")
    hooks = load_config(path)

    def install(scope, ctx):
        async def listener(invocation, next_):
            selected = [(command, timeout) for pattern, command, timeout in hooks if pattern.search(invocation.name)]
            pending = None
            try:
                for command, timeout in selected:
                    kind, reason = await asyncio.to_thread(run_hook, command, timeout, invocation, dialect, model)
                    if kind == DecisionKind.DENY:
                        return PreExecuteDecision(kind, reason or "Hook 拒绝了工具调用")
                    if kind == DecisionKind.ASK:
                        pending = PreExecuteDecision(kind, reason or "Hook 请求审批")
            except Exception:
                return PreExecuteDecision(DecisionKind.DENY, "Hook 检查失败，工具调用已拒绝")
            downstream = await next_()
            return pending if pending and downstream.kind == DecisionKind.ALLOW else downstream

        ctx.on("tools/pre-execute", listener)
    return install
