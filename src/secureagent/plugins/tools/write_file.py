"""Install the write_file tool."""
from secureagent.tools.write_file import WRITE, write_file


def installer(policy, observations):
    def install(scope, ctx):
        fs = ctx.fs
        scope.register(WRITE, lambda args: write_file(policy.workspace, observations, args["path"], args["content"], file_system=fs))
    return install
