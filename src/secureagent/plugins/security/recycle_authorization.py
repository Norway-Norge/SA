"""Authorization rules for the independently installed recycle tools."""

from secureagent.security.recycle_access import authorize_conversion, resolve_delete_target, valid_recycle_id


def installer(workspace):
    def install(scope, ctx):
        def active(name, check):
            def authorize(args):
                if ctx.tools.get(name) is None:
                    return False
                return check(args)
            return authorize
        scope.register_rule("safe_delete", active("safe_delete", lambda args: resolve_delete_target(workspace, args["path"])), writes=True)
        scope.register_rule("restore_file", active("restore_file", lambda args: valid_recycle_id(args["recycle_id"])), writes=True)
        scope.register_rule("convert_file", active("convert_file", lambda args: authorize_conversion(workspace, args)), writes=True)
        scope.register_rule("list_recycle_bin", active("list_recycle_bin", lambda args: None))
    return install
