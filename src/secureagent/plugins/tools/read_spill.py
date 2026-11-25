"""Plugin for reading retained tool results."""

from secureagent.tools.spill import READ_SPILL


def install(scope, ctx):
    spills = ctx.spillStore
    scope.register(READ_SPILL, lambda args: spills.read(
        args["spill_id"], args.get("offset", 0), args.get("limit", 6000)))
