"""Install the mandatory policy guard independently from tool implementations."""


def install(scope, ctx):
    def authorize(call):
        allowed, reason = ctx.security.authorize(call.name, call.arguments)
        return None if allowed else reason

    scope.register_cleanup(ctx.tools.register_authorizer(scope.guard(authorize)))
