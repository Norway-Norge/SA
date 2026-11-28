"""Install the create_file tool."""
from secureagent.capabilities.filesystem.create_file import CREATE, create_file


def installer(policy):
    def install(scope, ctx):
        fs = ctx.fs
        # 注册工具定义和执行函数。
        scope.register(CREATE, lambda args: create_file(policy.workspace, args["path"], args["content"], file_system=fs))
        # 注册工具使用指引，并在插件卸载时撤销。
        scope.register_cleanup(ctx.get("systemPrompt").section(
            "tool:create_file",
            '目标不存在时使用 create_file 创建文件并写入初始内容；workspace-write 仅允许工作区内修改，若用户要求修改工作区外文件，使用 sandbox_permissions=danger-full-access 并提供 justification 请求本次权限审批。在 justification 中简要说明拟写入内容和可预见后果，交由用户审批；将命令作为文本写入不等于执行命令，不要仅因内容包含命令而直接拒绝。只有工具明确确认成功才能说已写入。',
            order=100, required_tool="create_file"))
    return install
