"""Plugin for loading discovered local skills."""

from secureagent.tools.skill import SKILL


def install(scope, ctx):
    skills = ctx.skills
    scope.register(SKILL, lambda args: skills.load(args["name"]))
