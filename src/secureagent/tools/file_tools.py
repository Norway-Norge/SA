"""Register the built-in workspace file tools with their shared dependencies."""

from secureagent.tools.create_file import create_file
from secureagent.tools.edit_file import propose_edit
from secureagent.tools.filesystem import glob_files, read_file, search_files
from secureagent.tools.spec import CREATE, EDIT, GLOB, READ, SEARCH, WRITE
from secureagent.tools.write_file import write_file


def register_file_read_tools(registry, policy, observations, spills, file_system=None):
    registry.register(READ, lambda args: read_file(
        policy.workspace, args["path"], args.get("offset", 1), args.get("limit", 2000),
        observations.record, file_system=file_system))
    registry.register(SEARCH, lambda args: search_files(
        policy.workspace, args["path"], args["query"], spills))


def register_file_glob_tool(registry, policy, spills):
    registry.register(GLOB, lambda args: glob_files(
        policy.workspace, args["pattern"], args.get("path", "."), spills))


def register_file_write_tools(registry, policy, observations, get_edit_approvals, file_system=None):
    registry.register(CREATE, lambda args: create_file(
        policy.workspace, args["path"], args["content"], file_system=file_system))
    registry.register(WRITE, lambda args: write_file(
        policy.workspace, observations, args["path"], args["content"], file_system=file_system))
    registry.register(EDIT, lambda args: propose_edit(
        policy, observations, get_edit_approvals(), args["path"], args["old_string"],
        args["new_string"], args.get("replace_all", False), file_system=file_system))
