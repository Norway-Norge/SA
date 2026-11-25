"""Install the create_file tool."""
from secureagent.tools.create_file import CREATE, create_file


def installer(policy):
    def install(scope, ctx):
        fs = ctx.fs
        scope.register(CREATE, lambda args: create_file(policy.workspace, args["path"], args["content"], file_system=fs))
    return install
