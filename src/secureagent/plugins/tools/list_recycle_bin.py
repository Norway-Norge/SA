"""Install the list_recycle_bin tool."""
from secureagent.tools.safe_delete import LIST


def installer(operations):
    def install(scope, ctx):
        # 注册工具定义和执行函数。
        scope.register(LIST, lambda args: operations.list_items())
        # 注册工具使用指引，并在插件卸载时撤销。
        scope.register_cleanup(ctx.get("systemPrompt").section(
            "tool:list_recycle_bin",
            '需要查看可恢复的回收项目时使用 list_recycle_bin；列出的项目不表示已经恢复。',
            order=100, required_tool="list_recycle_bin"))
    return install
