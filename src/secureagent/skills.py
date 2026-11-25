"""Discover local skill metadata without loading it into the model."""




import os
import re
from pathlib import Path

import yaml

from secureagent.security.workspace import Workspace, WorkspaceError, is_link_or_reparse


ROOTS = (".sa/skills", ".agents/skills")
MAX_ENTRIES = 200
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")


class UniqueSafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        keys = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in keys:
                raise ValueError("元数据字段重复")
            keys.add(key)
        return super().construct_mapping(node, deep=deep)


def invocation_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    raise ValueError("Skill 调用设置无效")


def discover_skills(workspace: Workspace, *, user_home: Path | None = None) -> dict:
    skills = []
    issues = []
    truncated = False
    locations = [(workspace, root, root, False) for root in ROOTS]
    if user_home is not None:
        locations.append((Workspace(user_home), ".agents/skills", "用户目录 .agents/skills", True))
    for owner, root, source, absolute in locations:
        root_path = owner.root / root
        if not os.path.lexists(root_path):
            continue
        try:
            directory = owner.resolve(root, directory=True)
            with os.scandir(directory) as entries:
                for index, entry in enumerate(entries):
                    if index >= MAX_ENTRIES:
                        truncated = True
                        break
                    relative = f"{root}/{entry.name}"
                    display_path = str(owner.root / relative) if absolute else relative
                    try:
                        candidate = directory / entry.name
                        if is_link_or_reparse(candidate):
                            issues.append({"path": display_path, "error": "不支持符号链接或连接点"})
                            continue
                        if not entry.is_dir(follow_symlinks=False):
                            continue
                        skill_file = candidate / "SKILL.md"
                        if not os.path.lexists(skill_file):
                            continue
                        resolved = owner.resolve(f"{relative}/SKILL.md")
                        with resolved.open("rb") as stream:
                            if stream.readline().decode("utf-8-sig").strip() != "---":
                                raise ValueError("缺少元数据")
                            lines = []
                            for raw_line in stream:
                                line = raw_line.decode("utf-8")
                                if line.strip() == "---":
                                    break
                                lines.append(line)
                            else:
                                raise ValueError("元数据未结束")
                        data = yaml.load("".join(lines), Loader=UniqueSafeLoader)
                        if not isinstance(data, dict):
                            raise ValueError("元数据格式无效")
                        name, description = data.get("name"), data.get("description")
                        if not isinstance(name, str) or not NAME.fullmatch(name):
                            raise ValueError("名称须由小写字母、数字及连字符组成")
                        if not isinstance(description, str) or not description.strip():
                            raise ValueError("描述不能为空")
                        disabled = invocation_flag(data["disable-model-invocation"]) if "disable-model-invocation" in data else False
                        if "user-invocable" in data:
                            invocation_flag(data["user-invocable"])
                        skills.append({"name": name, "description": description.strip(), "source": source,
                                       "path": str(Path(display_path) / "SKILL.md"), "conflict": False,
                                       "model_invocable": not disabled})
                    except (OSError, UnicodeError, TypeError, RecursionError, yaml.YAMLError):
                        issues.append({"path": str(Path(display_path) / "SKILL.md"), "error": "无法读取或 Skill 格式无效"})
                    except ValueError as exc:
                        issues.append({"path": str(Path(display_path) / "SKILL.md"), "error": str(exc)})
        except (OSError, WorkspaceError):
            issues.append({"path": str(root_path) if absolute else root, "error": "目录不可用或超出允许范围"})
    counts = {}
    for skill in skills:
        counts[skill["name"]] = counts.get(skill["name"], 0) + 1
    for skill in skills:
        skill["conflict"] = counts[skill["name"]] > 1
    skills.sort(key=lambda skill: (skill["name"], skill["path"]))
    return {"skills": skills, "issues": issues, "truncated": truncated}



def load_skill(workspace: Workspace, name: str, *, user_home: Path | None = None) -> dict:
    """Compatibility entry point for the skill tool."""
    from secureagent.tools.skill import load_skill as load
    return load(workspace, name, user_home=user_home)
