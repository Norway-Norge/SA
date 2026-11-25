"""Register the DeepSeek fetch provider with ctx.web."""

from secureagent.tools.web_fetch import DeepSeekFetchProvider


def install(scope, ctx):
    provider = DeepSeekFetchProvider()
    provider.fetch = scope.guard(provider.fetch)
    # 注册能力提供方，并在插件卸载时撤销注册。
    scope.register_cleanup(ctx.web.register_fetch_provider("deepseek-fetch-api", provider))
