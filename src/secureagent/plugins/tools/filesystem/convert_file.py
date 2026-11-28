"""Install the convert_file tool."""
from secureagent.capabilities.filesystem.safe_delete import CONVERT


def installer(operations):
    def install(scope, ctx):
        scope.register_cleanup(operations.bind_decisions(("convert",), scope.guard(operations._decide)))
        # 注册工具定义和执行函数。
        scope.register(CONVERT, lambda args: operations.propose_convert(args["source_path"], args["target_path"], args["content"]))
        # 注册工具使用指引，并在插件卸载时撤销。
        scope.register_cleanup(ctx.get("systemPrompt").section(
            "tool:convert_file",
            '用户要求把现有文件转换为新格式且要替换原文件时，先读取源文件，再用 convert_file；目标已存在时应告知用户，不得覆盖。返回 REVIEW 表示尚未执行，应提醒用户审批，不要重复申请。转换内容是数据，转换本身不执行其中的代码或命令。',
            order=100, required_tool="convert_file"))
    return install
