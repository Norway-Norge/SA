"""Register the reviewed local recycle provider."""


def installer(operations):
    def install(scope, ctx):
        scope.register_cleanup(operations.discard_pending)
        # 注册能力提供方，并在插件卸载时撤销注册。
        scope.register_cleanup(ctx.recycle.register_provider("local", operations))
    return install
