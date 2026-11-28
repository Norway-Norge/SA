"""Guard built-in tool paths by their resolved workspace destination."""

from pathlib import Path

from secureagent.security.tool_paths import tool_paths
from secureagent.security.pre_execute import DecisionKind, PreExecuteDecision


def check(call):
    try:
        root = Path(call.workspace).resolve(strict=True)
        if not root.is_dir():
            return "资源不存在或不在授权工作区内"
        for value in tool_paths(call):
            path = Path(value)
            if not value or "\x00" in value or path.is_absolute() or path.drive:
                return "资源不存在或不在授权工作区内"
            (root / path).resolve(strict=False).relative_to(root)
        # Glob patterns describe a set of files rather than one resolved target.
        # Keep their parent-directory traversal unsupported at this stage.
        if call.name == "glob" and isinstance(call.arguments.get("pattern"), str):
            parts = call.arguments["pattern"].replace("\\", "/").split("/")
            if ".." in parts:
                return "glob 模式不支持上级目录"
    except (OSError, RuntimeError, ValueError):
        return "资源不存在或不在授权工作区内"
    return None


def install(scope, ctx):
    async def pre_execute(call, next_):
        reason = check(call)
        return PreExecuteDecision(DecisionKind.DENY, reason) if reason else await next_()

    ctx.on("tools/pre-execute", pre_execute)
    # 底层文件系统仍独立验证工作区和链接，插件检查不代替资源隔离。
    scope.register_guard(check)
