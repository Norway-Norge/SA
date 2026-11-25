"""Install the read_file tool."""
from secureagent.tools.read_file import READ, read_file


def installer(policy, observations):
    def install(scope, ctx):
        fs = ctx.fs
        # 注册工具定义和执行函数。
        scope.register(READ, lambda args: read_file(policy.workspace, args["path"],
            args.get("offset", 1), args.get("limit", 2000), observations.record, file_system=fs))
        # 注册工具使用指引，并在插件卸载时撤销。
        scope.register_cleanup(ctx.systemPrompt.section("tool:read_file",
            "读取工作区文本文件时使用 read_file；长文件可用 offset 和 limit 按行继续读取。",
            order=100, required_tool="read_file"))
    return install
