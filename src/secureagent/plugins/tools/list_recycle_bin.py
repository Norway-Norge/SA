"""Install the list_recycle_bin tool."""
from secureagent.tools.safe_delete import LIST


def installer(operations):
    def install(scope, ctx):
        scope.register_rule("list_recycle_bin", lambda args: None)
        scope.register(LIST, lambda args: operations.list_items())
    return install
