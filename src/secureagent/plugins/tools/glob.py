"""Install the glob tool."""
from secureagent.tools.glob import GLOB


def install(scope, ctx):
    search = ctx.fileSearch
    scope.register(GLOB, lambda args: search.glob(args["pattern"], args.get("path", ".")))
    scope.register_cleanup(ctx.get("systemPrompt").section(
        "tool:glob",
        '用户要求查看工作区目录结构时，可用 glob 枚举文件并按路径组织；glob 不返回目录或空目录且会排除部分路径，结果截断时如实说明，不要声称完整列出。',
        order=100, required_tool="glob"))
