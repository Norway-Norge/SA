"""Register the local filesystem provider beneath workspace restrictions."""

from secureagent.capabilities.filesystem.file_service import LocalFileSystem


def installer(workspace):
    def install(scope, ctx):
        provider = LocalFileSystem(workspace.root)
        # 注册能力提供方，并在插件卸载时撤销注册。
        scope.register_cleanup(ctx.fs.register_provider(provider))
    return install
