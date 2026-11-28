"""PowerShell tool plugin."""

from secureagent.capabilities.shell.pwsh import PWSH


def install(scope, ctx):
    def execute(args):
        command = args["command"]
        if not command.strip() or not args["description"].strip() or len(command) > 12000:
            return {"error": "命令或用途无效"}
        return ctx.shell.execute(command, args.get("timeout", 60))

    scope.register_rule("pwsh", lambda args: ctx.tools.get("pwsh") is not None)
    scope.register(PWSH, execute)
    scope.register_cleanup(ctx.systemPrompt.section(
        "tool:pwsh", "pwsh 在宿主机以 SA 进程权限执行，每次需用户审批；超时后结果可能未知，不要盲目重试。",
        order=100, required_tool="pwsh"))
