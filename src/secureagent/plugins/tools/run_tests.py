"""Plugin for workspace test execution."""

from secureagent.tools.shell import TESTS


def install(scope, ctx):
    runner = ctx.testRunner
    scope.register(TESTS, lambda args: runner.run(args["path"], args["runner"]))
