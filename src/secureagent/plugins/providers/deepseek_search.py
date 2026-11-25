"""Register the DeepSeek search provider with ctx.web."""

from secureagent.tools.web_search import DeepSeekSearchProvider


def install(scope, ctx):
    provider = DeepSeekSearchProvider()
    provider.search = scope.guard(provider.search)
    # 注册能力提供方，并在插件卸载时撤销注册。
    scope.register_cleanup(ctx.web.register_search_provider("deepseek-search-api", provider))
