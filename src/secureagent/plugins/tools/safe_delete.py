"""Install the safe_delete tool."""
from secureagent.tools.safe_delete import DELETE


def installer(operations):
    def install(scope, ctx):
        scope.register_rule("safe_delete", lambda args: operations._resolve_target(args["path"]), writes=True)
        scope.register_cleanup(operations.bind_decisions(("delete", "delete_directory"), scope.guard(operations._decide)))
        scope.register(DELETE, lambda args: operations.propose_delete(args["path"]))
        scope.register_cleanup(ctx.get("systemPrompt").section(
            "tool:safe_delete",
            'safe_delete 返回 REVIEW 时尚未执行，应提醒用户审批，不要重复申请；只有明确确认成功后才能声称删除完成。',
            order=100, required_tool="safe_delete"))
    return install
