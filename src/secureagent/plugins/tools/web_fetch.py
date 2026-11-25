"""Plugin for fetching web pages."""

from secureagent.tools.web_fetch import WEB_FETCH, web_fetch


def install(scope, ctx):
    web = ctx.web
    scope.register(WEB_FETCH, lambda args: web_fetch(args["url"], web))
