"""Register the DeepSeek search provider with ctx.web."""

from secureagent.tools.web_search import DeepSeekSearchProvider


def install(scope, ctx):
    provider = DeepSeekSearchProvider()
    provider.search = scope.guard(provider.search)
    scope.register_cleanup(ctx.web.register_search_provider("deepseek-search-api", provider))
