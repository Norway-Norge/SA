"""Independently installed guard for sensitive workspace tool paths."""

import json
from pathlib import Path

from secureagent.security.pre_execute import DecisionKind, PreExecuteDecision
from secureagent.security.tool_paths import tool_paths


def _load_rules():
    rules = json.loads(Path(__file__).with_name("sensitive_path_rules.json").read_text(encoding="utf-8"))
    expected = {"directories", "filenames", "suffixes", "name_prefixes", "stem_suffixes"}
    if not isinstance(rules, dict) or set(rules) != expected:
        raise ValueError("敏感路径插件规则配置无效")
    if any(not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value)
           for value in rules.values()):
        raise ValueError("敏感路径插件规则配置无效")
    return {key: tuple(item.casefold() for item in value) for key, value in rules.items()}


RULES = _load_rules()


def is_sensitive_component(value):
    name = value.casefold()
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return (name in RULES["directories"] or name in RULES["filenames"] or stem in RULES["filenames"]
            or name.startswith(RULES["name_prefixes"])
            or stem.endswith(RULES["stem_suffixes"]) or name.endswith(RULES["suffixes"]))


class SensitivePathPolicy:
    def denies(self, path):
        return any(is_sensitive_component(part) for part in path.parts)


def check(call):
    policy = SensitivePathPolicy()
    for path in tool_paths(call):
        if policy.denies(Path(path)):
            return "资源不存在或不在授权工作区内"
    return None


def install(scope, ctx):
    async def pre_execute(call, next_):
        reason = check(call)
        return PreExecuteDecision(DecisionKind.DENY, reason) if reason else await next_()

    # 在通用前置链中识别调用，并用单调守卫维持强制拒绝。
    ctx.on("tools/pre-execute", pre_execute)
    scope.register_guard(check)
    security = ctx.get("security")
    if security is not None:
        scope.register_cleanup(security.register_entry_filter(scope.guard(is_sensitive_component)))
