"""Install the safe_delete tool."""
from secureagent.tools.safe_delete import DELETE


def installer(operations):
    def install(scope, ctx):
        scope.register_rule("safe_delete", lambda args: operations._resolve_target(args["path"]), writes=True)
        scope.register_cleanup(operations.bind_decisions(("delete", "delete_directory"), scope.guard(operations._decide)))
        scope.register(DELETE, lambda args: operations.propose_delete(args["path"]))
    return install
