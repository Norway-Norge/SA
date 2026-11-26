"""Cordis access to the runtime's current authorization policy."""

from cordis_py import Service
from secureagent.security.pre_execute import ToolInvocation, freeze


class SecurityService(Service):
    def __init__(self, ctx, get_policy):
        self._get_policy = get_policy
        super().__init__(ctx, "security")

    def authorize(self, name, args):
        return self._get_policy().authorize(name, args)

    def reauthorize(self, name, args):
        """Recheck monotonic guards when a previously approved action commits."""
        policy = self._get_policy().resolve()
        call = ToolInvocation(name=name, arguments=freeze(args), tool_call_id="",
                              session_id=policy.session_id, workspace=str(policy.workspace_root),
                              permission_mode=policy.mode)
        return self.ctx.tools.check_guards(call)
