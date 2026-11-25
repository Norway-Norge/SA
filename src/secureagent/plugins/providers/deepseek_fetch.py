"""Register the DeepSeek fetch provider with ctx.web."""

from secureagent.tools.web_fetch import DeepSeekFetchProvider


def install(scope, ctx):
    provider = DeepSeekFetchProvider()
    provider.fetch = scope.guard(provider.fetch)
    scope.register_cleanup(ctx.web.register_fetch_provider("deepseek-fetch-api", provider))
