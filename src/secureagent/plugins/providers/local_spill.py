"""Register the local session spill provider."""

from secureagent.tools.spill import SpillStore


def installer(workspace):
    def install(scope, ctx):
        store = SpillStore(workspace)
        class LocalSpill:
            pass
        provider = LocalSpill()
        provider.save = scope.guard(store.save)
        provider.read = scope.guard(store.read)
        provider.retain = scope.guard(store.retain)
        scope.register_cleanup(ctx.spillStore.register_provider("local", provider))
    return install
