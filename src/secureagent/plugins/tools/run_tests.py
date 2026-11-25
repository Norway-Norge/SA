"""Plugin for workspace test execution."""

from secureagent.tools.shell import TESTS


def install(scope, ctx):
    runner = ctx.testRunner
    scope.register(TESTS, lambda args: runner.run(args["path"], args["runner"]))
    scope.register_cleanup(ctx.get("systemPrompt").section(
        "tool:run_tests",
        'run_tests 使用会话首次测试时的工作区快照，不要声称它测试了后续宿主机更改。',
        order=100, required_tool="run_tests"))
