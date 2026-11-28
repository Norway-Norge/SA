"""Observe sensitive read_file attempts without changing authorization.

Inspired by the DSH community's dsh-guard-sensitive-paths and secret-guard:
https://github.com/TARS-snail/dsh-guard-sensitive-paths
https://github.com/xiayuhkust/dsh-shell-kit
"""

import logging
from pathlib import PurePosixPath, PureWindowsPath

from secureagent.plugins.security.sensitive_path_guard import is_sensitive_component

log = logging.getLogger("secureagent.sensitive_read_monitor")


def is_sensitive_read(call):
    """Classify lexical components without reading or resolving the target."""
    if call.name != "read_file":
        return False
    path = call.arguments.get("path")
    if not isinstance(path, str):
        return False
    return any(is_sensitive_component(part) for part in
               (*PurePosixPath(path).parts, *PureWindowsPath(path).parts))


def install(scope, ctx):
    async def observe(call, next_):
        if is_sensitive_read(call):
            # 只记录读取尝试，不记录路径、参数、会话标识或文件内容。
            log.warning("检测到模型尝试通过 read_file 读取敏感路径；访问由后续策略决定")
        return await next_()

    # 插件卸载时由运行时撤销监听器；仅观察，不改变决策。
    ctx.on("tools/pre-execute", observe, prepend=True)
