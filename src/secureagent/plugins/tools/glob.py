"""Install the glob tool."""
from secureagent.tools.glob import GLOB


def install(scope, ctx):
    search = ctx.fileSearch
    scope.register(GLOB, lambda args: search.glob(args["pattern"], args.get("path", ".")))
