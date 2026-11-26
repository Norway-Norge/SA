"""Authorization ownership for email tools, independent of their handlers."""


def install(scope, ctx):
    for name in ("send_email", "check_email_operation"):
        scope.register_rule(name, lambda args, name=name: ctx.tools.get(name) is not None)
