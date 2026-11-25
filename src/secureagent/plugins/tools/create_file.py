"""Install the create_file tool."""
from secureagent.tools.create_file import CREATE, create_file


def installer(policy):
    def install(scope, ctx):
        fs = ctx.fs
        scope.register(CREATE, lambda args: create_file(policy.workspace, args["path"], args["content"], file_system=fs))
        scope.register_cleanup(ctx.get("systemPrompt").section(
            "tool:create_file",
            '目标不存在时使用 create_file 创建文件并写入初始内容；只有工具明确确认成功才能说已写入。',
            order=100, required_tool="create_file"))
    return install
