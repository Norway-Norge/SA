"""Plugin for isolated inline tests."""

from secureagent.capabilities.testing.inline_tests import INLINE_TESTS


def install(scope, ctx):
    runner = ctx.testRunner
    # 注册工具定义和执行函数。
    scope.register(INLINE_TESTS, lambda args: runner.run_inline(args["code"], args["tests"]))
    # 注册工具使用指引，并在插件卸载时撤销。
    scope.register_cleanup(ctx.get("systemPrompt").section(
        "tool:run_inline_tests",
        '用户要求生成并测试代码但不创建宿主文件时，使用 run_inline_tests 在一次性容器测试，不要创建工作区文件；测试后在回复中输出程序代码和测试结果，不要输出临时测试代码。',
        order=100, required_tool="run_inline_tests"))
