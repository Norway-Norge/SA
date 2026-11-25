"""Register the reviewed local recycle provider."""


def installer(operations):
    def install(scope, ctx):
        scope.register_cleanup(operations.discard_pending)
        scope.register_cleanup(ctx.recycle.register_provider("local", operations))
    return install
