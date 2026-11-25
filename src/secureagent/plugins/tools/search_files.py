"""Install the search_files tool."""
from secureagent.tools.search_files import SEARCH


def install(scope, ctx):
    search = ctx.fileSearch
    scope.register(SEARCH, lambda args: search.search(args["path"], args["query"]))
