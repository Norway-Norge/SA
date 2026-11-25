"""Install the edit_file tool and its approval handler."""
from secureagent.tools.edit_file import EDIT, decide_edit, propose_edit


def installer(policy, observations, get_approvals, get_policy, set_decide):
    def install(scope, ctx):
        fs = ctx.fs
        set_decide(scope.guard(lambda approval_id, approve: decide_edit(
            get_policy(), observations, get_approvals(), approval_id, approve, file_system=fs)))
        scope.register(EDIT, lambda args: propose_edit(policy, observations, get_approvals(), args["path"],
            args["old_string"], args["new_string"], args.get("replace_all", False), file_system=fs))
    return install
