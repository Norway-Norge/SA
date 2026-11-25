"""Plugin for isolated inline tests."""

from secureagent.tools.inline_tests import INLINE_TESTS


def install(scope, ctx):
    runner = ctx.testRunner
    scope.register(INLINE_TESTS, lambda args: runner.run_inline(args["code"], args["tests"]))
