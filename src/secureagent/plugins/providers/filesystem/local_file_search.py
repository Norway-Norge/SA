"""Workspace-confined file search provider."""

from secureagent.capabilities.filesystem.glob import glob_files
from secureagent.capabilities.filesystem.search_files import search_files


def installer(workspace, spills):
    def install(scope, ctx):
        class LocalFileSearch:
            def search(self, path, query):
                return search_files(workspace, path, query, spills)

            def glob(self, pattern, path):
                return glob_files(workspace, pattern, path, spills)

        provider = LocalFileSearch()
        provider.search = scope.guard(provider.search)
        provider.glob = scope.guard(provider.glob)
        # 注册能力提供方，并在插件卸载时撤销注册。
        scope.register_cleanup(ctx.fileSearch.register_provider("local", provider))
    return install
