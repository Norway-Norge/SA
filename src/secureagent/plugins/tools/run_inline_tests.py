"""Plugin for isolated inline tests."""

from secureagent.tools.inline_tests import INLINE_TESTS


def install(scope, ctx):
    runner = ctx.testRunner
    scope.register(INLINE_TESTS, lambda args: runner.run_inline(args["code"], args["tests"]))
    scope.register_cleanup(ctx.get("systemPrompt").section(
        "tool:run_inline_tests",
        '用户要求生成并测试代码但不创建宿主文件时，使用 run_inline_tests 在一次性容器测试，不要创建工作区文件；测试后在回复中输出程序代码和测试结果，不要输出临时测试代码。',
        order=100, required_tool="run_inline_tests"))
