"""Resource checks shared by recycle authorization and operation boundaries."""

import uuid
from secureagent.security.workspace import WorkspaceError


def resolve_delete_target(workspace, path):
    try:
        return workspace.resolve(path)
    except WorkspaceError:
        target = workspace.resolve(path, directory=True)
        if target == workspace.root:
            raise WorkspaceError("不允许删除工作区根目录")
        return target


def valid_recycle_id(value):
    try:
        return isinstance(value, str) and uuid.UUID(value).hex == value
    except (ValueError, AttributeError):
        return False


def authorize_conversion(workspace, args):
    workspace.resolve(args["source_path"])
    workspace.resolve_new_file(args["target_path"])
