"""Plugin for loading discovered local skills."""

from secureagent.tools.skill import SKILL


def install(scope, ctx):
    skills = ctx.skills
    scope.register(SKILL, lambda args: skills.load(args["name"]))
    scope.register_cleanup(ctx.get("systemPrompt").section(
        "tool:skill",
        '需要使用本地技能时调用 skill 加载正文；如果结果被保存，先读取完成任务所需的内容，不要将预览当作完整技能正文。',
        order=100, required_tool="skill"))
