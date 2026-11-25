"""Install the restore_file tool."""
from secureagent.tools.safe_delete import RESTORE


def installer(operations):
    def install(scope, ctx):
        scope.register_rule("restore_file", lambda args: operations._valid_id(args["recycle_id"]), writes=True)
        scope.register_cleanup(operations.bind_decisions(("restore", "restore_directory"), scope.guard(operations._decide)))
        scope.register(RESTORE, lambda args: operations.propose_restore(args["recycle_id"]))
    return install
