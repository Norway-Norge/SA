"""Model-facing PowerShell command definition."""

from secureagent.tools.spec import spec

PWSH = spec("pwsh", "在宿主机运行一次 PowerShell 命令（可能使用 Windows PowerShell 5.1）。命令以 SA 进程权限执行，不受文件工具工作区限制；每次需用户审批。为支持 UTF-8，命令前会加入编码设置，param、using 和 #requires 须改用脚本文件或适当包装。",
            {"command": {"type": "string", "description": "要执行的 PowerShell 命令。"},
             "description": {"type": "string", "description": "说明命令用途。"},
             "timeout": {"type": "integer", "minimum": 1, "maximum": 120, "description": "超时秒数，默认 60。"}},
            ["command", "description"])
