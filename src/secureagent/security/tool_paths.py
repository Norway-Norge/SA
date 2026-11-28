"""Path arguments of the built-in workspace tools, independent of tool dispatch."""

# This is an explicit list, not a claim that arbitrary MCP or plugin tools are covered.
PATH_FIELDS = {
    "read_file": ("path",),
    "search_files": ("path",),
    "glob": ("path",),
    "run_tests": ("path",),
    "create_file": ("path",),
    "write_file": ("path",),
    "edit_file": ("path",),
    "safe_delete": ("path",),
    "convert_file": ("source_path", "target_path"),
}


def tool_paths(call):
    for field in PATH_FIELDS.get(call.name, ()):
        value = call.arguments.get(field)
        if isinstance(value, str):
            yield value
