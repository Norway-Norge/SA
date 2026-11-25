"""Install the convert_file tool."""
from secureagent.tools.safe_delete import CONVERT


def installer(operations):
    def install(scope, ctx):
        scope.register_rule("convert_file", operations._authorize_convert, writes=True)
        scope.register_cleanup(operations.bind_decisions(("convert",), scope.guard(operations._decide)))
        scope.register(CONVERT, lambda args: operations.propose_convert(args["source_path"], args["target_path"], args["content"]))
    return install
