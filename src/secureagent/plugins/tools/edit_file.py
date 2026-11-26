"""Install the edit_file tool and its approval handler."""
from secureagent.tools.edit_file import EDIT, decide_edit, propose_edit


def installer(policy, observations, get_approvals, get_policy, set_decide):
    def install(scope, ctx):
        fs = ctx.fs
        set_decide(scope.guard(lambda approval_id, approve: decide_edit(
            get_policy(), observations, get_approvals(), approval_id, approve, file_system=fs,
            authorize=ctx.security.reauthorize)))
        # 注册工具定义和执行函数。
        scope.register(EDIT, lambda args: propose_edit(policy, observations, get_approvals(), args["path"],
            args["old_string"], args["new_string"], args.get("replace_all", False), file_system=fs,
            authorize=ctx.security.reauthorize))
        # 注册工具使用指引，并在插件卸载时撤销。
        scope.register_cleanup(ctx.get("systemPrompt").section(
            "tool:edit_file",
            '使用 edit_file 修改已有文件前先完整读取；返回 REVIEW 表示尚未执行，应提醒用户在界面审批。',
            order=100, required_tool="edit_file"))
    return install
