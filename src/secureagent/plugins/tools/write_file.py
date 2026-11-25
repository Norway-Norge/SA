"""Install the write_file tool."""
from secureagent.tools.write_file import WRITE, write_file


def installer(policy, observations):
    def install(scope, ctx):
        fs = ctx.fs
        scope.register(WRITE, lambda args: write_file(policy.workspace, observations, args["path"], args["content"], file_system=fs))
        scope.register_cleanup(ctx.get("systemPrompt").section(
            "tool:write_file",
            '使用 write_file 覆盖已有文件前先完整读取；只有工具明确确认成功才能说已写入，待审批或结果未知时不要盲目重试。',
            order=100, required_tool="write_file"))
    return install
