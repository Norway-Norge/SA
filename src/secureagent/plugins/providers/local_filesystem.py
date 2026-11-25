"""Register the local filesystem provider beneath workspace restrictions."""

from secureagent.tools.file_service import LocalFileSystem


def installer(workspace):
    def install(scope, ctx):
        provider = LocalFileSystem(workspace.root)
        scope.register_cleanup(ctx.fs.register_provider(provider))
    return install
