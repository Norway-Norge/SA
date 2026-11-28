"""Command-line entry point."""

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from openai import APIError, APIConnectionError

from secureagent.agent.graph import Agent
from secureagent.agent.model import ChatModel
from secureagent.security.policy import Policy
from secureagent.security.observations import read_session
from secureagent.security.workspace import Workspace, WorkspaceError
from secureagent.runtime.composition import create_dispatcher
from secureagent.plugins.security import cli_approval
from secureagent.capabilities.testing.shell import sandbox_available


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="SecureAgent 单 Agent 命令行助手")
    parser.add_argument("--workspace", required=True, type=Path, help="授权读取的工作区")
    parser.add_argument("--prompt", help="单次任务；不指定时进入多轮对话")
    parser.add_argument("--sandbox-image", help="本机已有的、可信的测试容器镜像；提供后才启用隔离测试工具")
    parser.add_argument("--sandbox-mode", choices=("read-only", "workspace-write", "danger-full-access"),
                        default=os.environ.get("SA_SANDBOX_MODE", "workspace-write"), help="宿主文件操作策略模式")
    args = parser.parse_args(argv)
    api_key = os.environ.get("SA_API_KEY")
    model_name = os.environ.get("SA_MODEL")
    if not api_key or not model_name:
        print("请设置 SA_API_KEY 和 SA_MODEL；可用 SA_BASE_URL 指定兼容接口。", file=sys.stderr)
        return 2
    try:
        workspace = Workspace(args.workspace)
    except (OSError, WorkspaceError) as exc:
        print(f"工作区不可用：{exc}", file=sys.stderr)
        return 2
    image = args.sandbox_image
    if image and not sandbox_available(image):
        print("隔离环境或本地镜像不可用，测试工具保持禁用。", file=sys.stderr)
        image = None
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        policy = Policy(workspace, args.sandbox_mode)
    except ValueError:
        print("SA_SANDBOX_MODE 无效。", file=sys.stderr)
        return 2
    model = ChatModel(api_key=api_key, model=model_name, base_url=os.environ.get("SA_BASE_URL") or None)
    agent = Agent(model, create_dispatcher(policy, image))
    history = None
    session_id = str(uuid.uuid4())
    if args.prompt is None:
        # Only the interactive CLI owns this session's one-shot approvals.
        agent.dispatcher.plugins.register_tool("cli-pre-execute-approval",
            cli_approval.installer(session_id), dependencies=("tools",))
    print(f"已授权工作区：{workspace.root}", file=sys.stderr)
    print("读取的工作区内容会发送给配置的模型服务。", file=sys.stderr)
    if not image:
        print("隔离测试工具未启用。", file=sys.stderr)
    while True:
        try:
            text = args.prompt if args.prompt is not None else input("你：").strip()
            if not text or text.lower() in {"exit", "quit"}:
                if args.prompt is not None:
                    return 0
                if not text:
                    continue
                return 0
            if args.prompt is None and text == "/reset-sandbox":
                if agent.dispatcher.sandboxes is None:
                    print("测试沙箱未启用。")
                elif input("清除本会话测试沙箱中的所有状态？输入 yes 确认：").strip().lower() == "yes":
                    result = agent.dispatcher.sandboxes.reset(session_id)
                    print(json.dumps(result, ensure_ascii=False))
                    if result.get("status") == "RESET" and history:
                        history.append({"role": "user", "content": "用户已重建测试沙箱；旧状态已清除，下次测试使用新的工作区快照。"})
                continue
            previous_length = len(history) if history else 0
            with read_session(session_id):
                state = agent.run(text, history)
            history = state["messages"]
            print("SA：" + (history[-1].get("content") or "模型未返回文本。"))
            recent = history[previous_length:]
            calls = {call.get("id"): call for message in recent for call in message.get("tool_calls", [])}
            for message in recent:
                if message.get("role") != "tool":
                    continue
                try:
                    result = json.loads(message.get("content", "{}"))
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(result, dict) or result.get("status") != "REVIEW":
                    continue
                if args.prompt is not None:
                    print("操作等待审批；单次模式退出后请求失效，请使用交互模式。")
                    continue
                call = calls.get(message.get("tool_call_id"), {})
                tool_name = call.get("function", {}).get("name")
                print("待审批操作：" + call.get("function", {}).get("arguments", "{}"))
                if result.get("action") in {"delete_directory", "restore_directory"}:
                    print("目录清单：" + json.dumps(result.get("entries", []), ensure_ascii=False))
                approved = input("批准此操作？输入 yes 批准，其他输入拒绝：").strip().lower() == "yes"
                with read_session(session_id):
                    if tool_name in {"safe_delete", "restore_file", "convert_file"}:
                        decision = agent.dispatcher.safe_delete.decide(result["approval_id"], approved)
                    elif tool_name == "send_email":
                        decision = agent.dispatcher.decide_email(result["approval_id"], approved)
                    else:
                        decision = agent.dispatcher.decide_edit(result["approval_id"], approved)
                print("审批结果：" + json.dumps(decision, ensure_ascii=False))
                status = decision.get("status") or decision.get("code") or "UNKNOWN"
                history.append({"role": "user", "content": f"用户{'批准' if approved else '拒绝'}了{tool_name}；执行状态：{status}。"})
            if args.prompt is not None:
                return 0
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        except (APIError, APIConnectionError, ValueError) as exc:
            print(f"执行失败：{type(exc).__name__}，请检查模型配置及连接。", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
