"""Register the email operation status lookup tool."""

from secureagent.tools.spec import spec

CHECK = spec("check_email_operation", "查询本会话邮件操作记录与可用的工具消息证据，不执行发送。",
             {"operation_id": {"type": "string"}}, ["operation_id"])


def installer(approvals):
    def install(scope, ctx):
        scope.register(CHECK, lambda args: approvals.check(args["operation_id"]))
        scope.register_cleanup(ctx.get("systemPrompt").section(
            "tool:check_email_operation", "邮件操作结果不明时查询 check_email_operation；"
            "工具消息不能证明投递，服务商无法查询时请用户核对发件箱，不要自行重试。",
            order=100, required_tool="check_email_operation"))
    return install
