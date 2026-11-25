"""Plugin for web search."""

from secureagent.tools.web_search import WEB_SEARCH, web_search


def install(scope, ctx):
    web = ctx.web
    scope.register(WEB_SEARCH, lambda args: web_search(args["query"], web))
