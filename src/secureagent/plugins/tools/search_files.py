"""Install the search_files tool."""
from secureagent.tools.search_files import SEARCH


def install(scope, ctx):
    search = ctx.fileSearch
    scope.register(SEARCH, lambda args: search.search(args["path"], args["query"]))
    scope.register_cleanup(ctx.get("systemPrompt").section(
        "tool:search_files",
        '需要在工作区文件中查找文字时使用 search_files；根据工具结果如实说明搜索范围和截断情况。',
        order=100, required_tool="search_files"))
