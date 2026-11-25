"""Plugin for workspace test execution."""

from secureagent.tools.shell import TESTS


def install(scope, ctx):
    runner = ctx.testRunner
    # 注册工具定义和执行函数。
    scope.register(TESTS, lambda args: runner.run(args["path"], args["runner"]))
    # 注册工具使用指引，并在插件卸载时撤销。
    scope.register_cleanup(ctx.get("systemPrompt").section(
        "tool:run_tests",
        'run_tests 使用会话首次测试时的工作区快照，不要声称它测试了后续宿主机更改。',
        order=100, required_tool="run_tests"))
