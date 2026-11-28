"""Install the restore_file tool."""
from secureagent.capabilities.filesystem.safe_delete import RESTORE


def installer(operations):
    def install(scope, ctx):
        scope.register_cleanup(operations.bind_decisions(("restore", "restore_directory"), scope.guard(operations._decide)))
        # 注册工具定义和执行函数。
        scope.register(RESTORE, lambda args: operations.propose_restore(args["recycle_id"]))
        # 注册工具使用指引，并在插件卸载时撤销。
        scope.register_cleanup(ctx.get("systemPrompt").section(
            "tool:restore_file",
            'restore_file 返回 REVIEW 时尚未执行，应提醒用户审批，不要重复申请；只有明确确认成功后才能声称恢复完成。',
            order=100, required_tool="restore_file"))
    return install
