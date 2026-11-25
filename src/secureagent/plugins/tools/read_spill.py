"""Plugin for reading retained tool results."""

from secureagent.tools.spill import READ_SPILL


def install(scope, ctx):
    spills = ctx.spillStore
    scope.register(READ_SPILL, lambda args: spills.read(
        args["spill_id"], args.get("offset", 0), args.get("limit", 6000)))
    scope.register_cleanup(ctx.get("systemPrompt").section(
        "tool:read_spill",
        '工具结果含 spill_id 时，可调用 read_spill 按 offset 分页读取保留的完整结果；不要把预览当作完整内容。',
        order=100, required_tool="read_spill"))
