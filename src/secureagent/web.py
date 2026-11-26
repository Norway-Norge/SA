"""Local web API for the SecureAgent workbench."""

import json
import os
import queue
import threading
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, StrictBool
from openai import APIConnectionError, APITimeoutError

from secureagent.agent.graph import Agent
from secureagent.agent.context import ContextCompactionError, ContextHandler, model_context
from secureagent.agent.reasoning import journal_events, reasoning_events, test_source_events, tool_events
from secureagent.agent.stream_redaction import StreamRedactor
from secureagent.agent.model import ChatModel
from secureagent.security.policy import Policy, SANDBOX_MODES
from secureagent.security.observations import read_session
from secureagent.security.email_evidence import email_evidence
from secureagent.security.workspace import Workspace, WorkspaceError
from secureagent.session_store import SessionStore, SessionStoreError, approval_outcome, display_tool_event, tool_process
from secureagent.plugins.composition import create_dispatcher
from secureagent.tools.shell import sandbox_available
from secureagent.workspaces import WorkspaceRegistry, choose_directory, workspace_storage
from secureagent.skills import discover_skills

load_dotenv()

@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    if agent is not None and agent.dispatcher.sandboxes is not None:
        agent.dispatcher.sandboxes.close()
    for selected in workspace_agents.values():
        if selected.dispatcher.sandboxes is not None:
            selected.dispatcher.sandboxes.close()


app = FastAPI(title="SecureAgent Workbench", docs_url=None, redoc_url=None, lifespan=lifespan)
session_directory = Path(os.environ.get("SA_SESSION_DIR") or Path.home() / ".secureagent" / "sessions")
session_store = SessionStore(session_directory)
agent: Agent | None = None
workspace_path: Path | None = None
workspace_registry = WorkspaceRegistry(session_directory.parent / "workspaces.json")
workspace_agents: dict[str, Agent] = {}
workspace_stores: dict[str, SessionStore] = {}
workspace_agents_lock = threading.RLock()
picker_lock = threading.Lock()
session_locks: dict[str, threading.Lock] = {}
session_locks_guard = threading.Lock()


@contextmanager
def session_operation(session_id: str | None):
    if not session_id:
        yield
        return
    normalized = session_store.normalize_id(session_id)
    if normalized is None:
        raise HTTPException(status_code=400, detail="会话标识无效。")
    with session_locks_guard:
        lock = session_locks.setdefault(normalized, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="会话正在执行，请等待完成后再操作。")
    try:
        yield
    finally:
        lock.release()


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = Field(default=None, max_length=64)
    workspace_id: str | None = Field(default=None, max_length=64)


class EditDecisionRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    approve: StrictBool


class SandboxResetRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


class SessionTitleRequest(BaseModel):
    title: str = Field(min_length=1, max_length=80)


class PersonaRequest(BaseModel):
    name: str | None = Field(max_length=128)
    workspace_id: str | None = Field(default=None, max_length=64)


def get_agent() -> Agent:
    global agent, workspace_path
    if agent is not None:
        return agent

    api_key = os.environ.get("SA_API_KEY")
    model_name = os.environ.get("SA_MODEL")
    workspace_value = os.environ.get("SA_WORKSPACE")
    if not api_key or not model_name:
        raise HTTPException(status_code=503, detail="请先在 .env 中配置 SA_API_KEY 和 SA_MODEL。")
    if not workspace_value:
        raise HTTPException(status_code=503, detail="请先在 .env 中配置 SA_WORKSPACE 授权目录。")

    try:
        workspace_path = Path(workspace_value).expanduser().resolve(strict=True)
        workspace = Workspace(workspace_path)
    except (OSError, WorkspaceError) as exc:
        raise HTTPException(status_code=503, detail="SA_WORKSPACE 不是可用的工作区目录。") from exc

    image = os.environ.get("SA_SANDBOX_IMAGE") or None
    if image and not sandbox_available(image):
        image = None
    try:
        policy = Policy(workspace, os.environ.get("SA_SANDBOX_MODE", "workspace-write"))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="SA_SANDBOX_MODE 无效。") from exc
    model = ChatModel(api_key=api_key, model=model_name,
                      base_url=os.environ.get("SA_BASE_URL") or None)
    try:
        cache = workspace_storage(workspace.root, "cache") if image else None
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="工作区缓存目录不可用。") from exc
    agent = Agent(model, create_dispatcher(policy, image, cache_dir=cache))
    return agent


def workspace_store(identity: str) -> SessionStore:
    try:
        directory = workspace_storage(workspace_registry.resolve(identity), "sessions")
        with workspace_agents_lock:
            if identity not in workspace_stores:
                workspace_stores[identity] = SessionStore(directory, workspace_id=identity)
            elif workspace_stores[identity].directory != directory:
                raise WorkspaceError("工作区存储目录已变化")
            return workspace_stores[identity]
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="所选工作区不可用或存储目录无效。") from exc


def selected_agent(identity: str | None) -> Agent:
    if identity is None:
        return get_agent()
    try:
        root = workspace_registry.resolve(identity)
        cache = workspace_storage(root, "cache")
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="所选工作区不可用或缓存目录无效。") from exc
    with workspace_agents_lock:
        if identity in workspace_agents:
            return workspace_agents[identity]
        api_key, model_name = os.environ.get("SA_API_KEY"), os.environ.get("SA_MODEL")
        if not api_key or not model_name:
            raise HTTPException(status_code=503, detail="请先在 .env 中配置 SA_API_KEY 和 SA_MODEL。")
        image = os.environ.get("SA_SANDBOX_IMAGE") or None
        if image and not sandbox_available(image):
            image = None
        try:
            policy = Policy(Workspace(root), os.environ.get("SA_SANDBOX_MODE", "workspace-write"))
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="工作区策略配置无效。") from exc
        model = ChatModel(api_key=api_key, model=model_name, base_url=os.environ.get("SA_BASE_URL") or None)
        workspace_agents[identity] = Agent(model, create_dispatcher(policy, image, cache_dir=cache))
        return workspace_agents[identity]


@app.get("/api/personas")
def get_personas(workspace_id: str | None = None):
    return selected_agent(workspace_id).dispatcher.personas.snapshot()


@app.put("/api/personas")
def select_persona(request: PersonaRequest):
    personas = selected_agent(request.workspace_id).dispatcher.personas
    try:
        personas.select(request.name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="人格不存在或已卸载。") from exc
    return personas.snapshot()


def locate_session(session_id: str, *, include_deleted: bool = False) -> tuple[str | None, SessionStore]:
    found: list[tuple[str | None, SessionStore]] = []
    unavailable = False
    if session_store.get_session(session_id, include_deleted=True) is not None:
        found.append((None, session_store))
    for item in workspace_registry.list():
        try:
            store = workspace_store(item["id"])
            if store.get_session(session_id, include_deleted=True) is not None:
                if found and found[0][0] is None and store.directory.resolve() == session_store.directory.resolve():
                    found.clear()
                found.append((item["id"], store))
        except HTTPException:
            unavailable = True
            continue
    if len(found) > 1:
        raise HTTPException(status_code=409, detail="会话标识存在冲突，无法确定工作区。")
    if not found and unavailable:
        raise HTTPException(status_code=503, detail="部分工作区不可用，无法确认会话归属。")
    if found and not include_deleted and found[0][1].get_session(session_id) is None:
        raise HTTPException(status_code=404, detail="会话已删除。")
    return found[0] if found else (None, session_store)


@app.get("/api/workspaces")
def list_workspaces() -> dict[str, Any]:
    default = os.environ.get("SA_WORKSPACE")
    try:
        if default:
            try:
                workspace_registry.register(default)
            except (OSError, ValueError):
                pass
        return {"workspaces": workspace_registry.list()}
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="无法读取或初始化工作区列表。") from exc


@app.post("/api/workspaces/pick")
def pick_workspace() -> dict[str, Any]:
    if not picker_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="文件夹选择窗口已打开。")
    try:
        chosen = choose_directory()
        if chosen is None:
            return {"cancelled": True}
        return {"workspace": workspace_registry.register(chosen)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="无法选择或初始化工作区，请检查目录权限。") from exc
    finally:
        picker_lock.release()


@app.get("/api/workspaces/{workspace_id}/skills")
def list_workspace_skills(workspace_id: str) -> dict[str, Any]:
    try:
        root = workspace_registry.resolve(workspace_id)
        return {"workspace_id": workspace_id, **discover_skills(Workspace(root), user_home=Path.home())}
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="工作区不存在或不可用。") from exc


@app.get("/api/health")
def health() -> dict[str, Any]:
    configured = bool(os.environ.get("SA_API_KEY") and os.environ.get("SA_MODEL")
                      and (os.environ.get("SA_WORKSPACE") or workspace_registry.list())
                      and os.environ.get("SA_SANDBOX_MODE", "workspace-write") in SANDBOX_MODES)
    tools = ["skill", "read_spill", "read_file", "search_files", "glob", "create_file", "write_file", "edit_file", "safe_delete", "list_recycle_bin", "restore_file", "convert_file"]
    from secureagent.tools.web_search import enabled as web_search_enabled
    if web_search_enabled():
        tools.append("web_search")
    from secureagent.tools.web_fetch import enabled as web_fetch_enabled
    if web_fetch_enabled():
        tools.append("web_fetch")
    image = os.environ.get("SA_SANDBOX_IMAGE") or None
    if image and sandbox_available(image):
        tools.extend(["run_tests", "run_inline_tests"])
    raw_context_window = os.environ.get("SA_CONTEXT_WINDOW", "")
    context_window = int(raw_context_window) if raw_context_window.isascii() and raw_context_window.isdecimal() and len(raw_context_window) <= 9 and int(raw_context_window) > 0 else None
    return {
        "ok": True,
        "configured": configured,
        "workspace": str(workspace_path) if workspace_path else os.environ.get("SA_WORKSPACE"),
        "model": os.environ.get("SA_MODEL", ""),
        "context_window": context_window,
        "sandbox_mode": agent.dispatcher.policy.resolve().mode if agent is not None else
                        os.environ.get("SA_SANDBOX_MODE", "workspace-write"),
        "tools": tools,
    }


@app.get("/api/sessions")
def list_sessions(include_deleted: bool = False) -> dict[str, Any]:
    try:
        sessions = [{**item, "workspace_id": None} for item in session_store.list_sessions(include_deleted=include_deleted)]
        for workspace in workspace_registry.list():
            try:
                store = workspace_store(workspace["id"])
                if store.directory.resolve() == session_store.directory.resolve():
                    sessions = [item for item in sessions if item["workspace_id"] is not None]
                sessions.extend({**item, "workspace_id": workspace["id"]} for item in store.list_sessions(include_deleted=include_deleted))
            except HTTPException:
                continue
        return {"sessions": sorted(sessions, key=lambda item: item["updated_at"], reverse=True)}
    except SessionStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict[str, Any]:
    try:
        identity, store = locate_session(session_id)
        record = store.get_session(session_id)
    except SessionStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="会话不存在。")
    return {**record, "workspace_id": identity}


@app.patch("/api/sessions/{session_id}")
def rename_session(session_id: str, request: SessionTitleRequest) -> dict[str, Any]:
    with session_operation(session_id):
        try:
            identity, store = locate_session(session_id)
            if store.get_session(session_id) is None:
                raise HTTPException(status_code=404, detail="会话不存在。")
            store.update_session(session_id, title=request.title)
            return {"session_id": session_id, "title": request.title.strip(), "workspace_id": identity}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except SessionStoreError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict[str, Any]:
    with session_operation(session_id):
        try:
            identity, store = locate_session(session_id)
            if store.get_session(session_id) is None:
                raise HTTPException(status_code=404, detail="会话不存在。")
            store.update_session(session_id, deleted=True)
            return {"session_id": session_id, "workspace_id": identity, "deleted": True}
        except SessionStoreError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/sessions/{session_id}/restore")
def restore_session(session_id: str) -> dict[str, Any]:
    with session_operation(session_id):
        try:
            identity, store = locate_session(session_id, include_deleted=True)
            record = store.get_session(session_id, include_deleted=True)
            if record is None or not record["deleted"]:
                raise HTTPException(status_code=404, detail="已删除的会话不存在。")
            store.update_session(session_id, deleted=False)
            return {"session_id": session_id, "workspace_id": identity, "deleted": False}
        except SessionStoreError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/sandbox/reset")
def reset_sandbox(request: SandboxResetRequest) -> dict[str, Any]:
    with session_operation(request.session_id):
        return _reset_sandbox(request)


def _reset_sandbox(request: SandboxResetRequest) -> dict[str, Any]:
    session_id = session_store.normalize_id(request.session_id)
    if session_id is None:
        raise HTTPException(status_code=400, detail="会话标识无效。")
    try:
        identity, store = locate_session(session_id)
        record = store.get_session(session_id)
    except SessionStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if record is None:
        raise HTTPException(status_code=404, detail="会话不存在。")
    sandboxes = selected_agent(identity).dispatcher.sandboxes
    if sandboxes is None:
        raise HTTPException(status_code=503, detail="测试沙箱未启用。")
    result = sandboxes.reset(session_id)
    if result.get("status") == "RESET":
        note = "用户已重建测试沙箱；旧沙箱状态已清除，下次测试将使用新的工作区快照。"
        try:
            store.append_turn(session_id, [{"role": "user", "content": note}],
                                      "重建测试沙箱", note, [])
        except SessionStoreError:
            result["warning"] = "沙箱已清除，但会话记录保存失败。"
    return result


@app.post("/api/edit-approvals/{approval_id}")
def resolve_edit_approval(approval_id: str, request: EditDecisionRequest) -> dict[str, Any]:
    with session_operation(request.session_id):
        return _resolve_edit_approval(approval_id, request)


def _resolve_edit_approval(approval_id: str, request: EditDecisionRequest) -> dict[str, Any]:
    session_id = session_store.normalize_id(request.session_id)
    if session_id is None or len(approval_id) > 100:
        raise HTTPException(status_code=400, detail="审批或会话标识无效。")
    identity, store = locate_session(session_id)
    current_agent = selected_agent(identity)
    with read_session(session_id):
        result = current_agent.dispatcher.decide_edit(approval_id, request.approve)
    if result.get("code") != "INVALID_APPROVAL":
        decision = "批准" if request.approve else "拒绝"
        outcome = result.get("status") or result.get("code") or "UNKNOWN"
        note = f"用户{decision}了文件编辑；执行状态：{outcome}。"
        try:
            audit_event = {"name": "edit_decision", "arguments": {"approval_id": approval_id}, "result": result}
            store.append_turn(session_id, [{"role": "user", "content": note}],
                                      f"{decision}文件编辑", note, [audit_event])
        except SessionStoreError:
            result["warning"] = "审批已处理，但会话记录保存失败；请先检查文件状态。"
        result["display_message"] = approval_outcome(result)
    return result


@app.get("/api/edit-approvals/{approval_id}")
def edit_approval_status(approval_id: str, session_id: str) -> dict[str, Any]:
    normalized = session_store.normalize_id(session_id)
    if normalized is None or len(approval_id) > 100:
        raise HTTPException(status_code=400, detail="审批或会话标识无效。")
    identity, store = locate_session(normalized)
    with read_session(normalized):
        status = selected_agent(identity).dispatcher.edit_approvals.status(approval_id)
    if status.get("status") != "UNKNOWN":
        return status
    try:
        turns = store.load_turns(normalized)
    except SessionStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    for turn in reversed(turns):
        for item in turn["ui"]["tools"]:
            if (isinstance(item, dict) and item.get("name") == "edit_decision"
                    and isinstance(item.get("arguments"), dict)
                    and item["arguments"].get("approval_id") == approval_id
                    and isinstance(item.get("result"), dict)):
                return item["result"]
    return status


@app.post("/api/recycle-approvals/{approval_id}")
def resolve_recycle_approval(approval_id: str, request: EditDecisionRequest) -> dict[str, Any]:
    with session_operation(request.session_id):
        return _resolve_recycle_approval(approval_id, request)


def _resolve_recycle_approval(approval_id: str, request: EditDecisionRequest) -> dict[str, Any]:
    session_id = session_store.normalize_id(request.session_id)
    if session_id is None or len(approval_id) > 100:
        raise HTTPException(status_code=400, detail="审批或会话标识无效。")
    identity, store = locate_session(session_id)
    plugin = selected_agent(identity).dispatcher.safe_delete
    if plugin is None:
        raise HTTPException(status_code=503, detail="安全删除插件未启用。")
    with read_session(session_id):
        result = plugin.decide(approval_id, request.approve)
    if result.get("code") != "INVALID_APPROVAL":
        note = f"用户{'批准' if request.approve else '拒绝'}了回收操作；执行状态：{result.get('status') or result.get('code') or 'UNKNOWN'}。"
        try:
            event = {"name": "recycle_decision", "arguments": {"approval_id": approval_id}, "result": result}
            store.append_turn(session_id, [{"role": "user", "content": note}], "处理回收审批", note, [event])
        except SessionStoreError:
            result["warning"] = "操作已处理，但会话记录保存失败；请先检查文件和回收区。"
        result["display_message"] = approval_outcome(result)
    return result


@app.get("/api/recycle-approvals/{approval_id}")
def recycle_approval_status(approval_id: str, session_id: str) -> dict[str, Any]:
    normalized = session_store.normalize_id(session_id)
    if normalized is None or len(approval_id) > 100:
        raise HTTPException(status_code=400, detail="审批或会话标识无效。")
    identity, store = locate_session(normalized)
    plugin = selected_agent(identity).dispatcher.safe_delete
    if plugin is None:
        raise HTTPException(status_code=503, detail="安全删除插件未启用。")
    with read_session(normalized):
        status = plugin.status(approval_id)
    if status.get("status") != "UNKNOWN":
        return status
    try:
        for turn in reversed(store.load_turns(normalized)):
            for item in turn["ui"]["tools"]:
                if (isinstance(item, dict) and item.get("name") == "recycle_decision"
                        and isinstance(item.get("arguments"), dict)
                        and item["arguments"].get("approval_id") == approval_id
                        and isinstance(item.get("result"), dict)):
                    return item["result"]
    except SessionStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return status


@app.post("/api/email-approvals/{approval_id}")
def resolve_email_approval(approval_id: str, request: EditDecisionRequest) -> dict[str, Any]:
    with session_operation(request.session_id):
        session_id = session_store.normalize_id(request.session_id)
        if session_id is None or len(approval_id) > 100:
            raise HTTPException(status_code=400, detail="审批或会话标识无效。")
        identity, store = locate_session(session_id)
        dispatcher = selected_agent(identity).dispatcher
        if dispatcher.decide_email is None:
            raise HTTPException(status_code=503, detail="邮件插件未启用。")
        with read_session(session_id):
            result = dispatcher.decide_email(approval_id, request.approve)
        if result.get("code") != "INVALID_APPROVAL":
            outcome = result.get("status", "UNKNOWN")
            note = f"用户{'批准' if request.approve else '拒绝'}了邮件发送；执行状态：{outcome}。"
            try:
                event = {"name": "email_decision", "arguments": {"approval_id": approval_id}, "result": result}
                store.append_turn(session_id, [{"role": "user", "content": note}], "处理邮件审批", note, [event])
            except SessionStoreError:
                result["warning"] = "审批已处理，但会话记录保存失败；请检查发件箱。"
            result["display_message"] = result.get("message", "邮件状态无法确认，请检查发件箱。")
        return result


@app.get("/api/email-approvals/{approval_id}")
def email_approval_status(approval_id: str, session_id: str) -> dict[str, Any]:
    normalized = session_store.normalize_id(session_id)
    if normalized is None or len(approval_id) > 100:
        raise HTTPException(status_code=400, detail="审批或会话标识无效。")
    identity, store = locate_session(normalized)
    approvals = selected_agent(identity).dispatcher.email_approvals
    if approvals is not None:
        with read_session(normalized):
            status = approvals.status(approval_id)
        if status.get("status") != "UNKNOWN":
            return status
    try:
        for turn in reversed(store.load_turns(normalized)):
            for item in turn["ui"]["tools"]:
                if (isinstance(item, dict) and item.get("name") == "email_decision"
                        and isinstance(item.get("arguments"), dict)
                        and item["arguments"].get("approval_id") == approval_id
                        and isinstance(item.get("result"), dict)):
                    return item["result"]
    except SessionStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"status": "UNKNOWN", "message": "审批状态不可用，请检查发件箱。"}


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict[str, Any]:
    return _run_chat(request)


@app.post("/api/chat/stream")
def chat_stream(request: ChatRequest) -> StreamingResponse:
    events: queue.Queue[dict[str, Any]] = queue.Queue()
    sections: list[str] = []
    context_usage: dict[str, Any] = {}
    max_chars = 128_000
    sources: list[str] = []
    redactor = StreamRedactor(sources)
    buffered_answer: list[str] = []
    disconnected = threading.Event()

    def send(event: dict[str, Any]) -> None:
        if not disconnected.is_set():
            events.put(event)

    def emit(kind: str, text: str) -> None:
        nonlocal redactor, buffered_answer
        if kind == "start":
            context_usage.clear()
            redactor = StreamRedactor(sources)
            buffered_answer = []
            sections.append("")
            send({"type": "reasoning_start"})
        elif kind == "usage":
            context_usage.update(json.loads(text))
            send({"type": "context_usage", "usage": dict(context_usage)})
        elif kind == "delta" and sections:
            remaining = max_chars - sum(len(section) for section in sections)
            text = text[:max(0, remaining)]
            if text:
                sections[-1] += text
                send({"type": "reasoning_delta", "text": text})
        elif kind == "text_delta":
            if len(set(sources)) > 1:
                buffered_answer.append(text)
            else:
                visible = redactor.push(text)
                if visible:
                    send({"type": "answer_delta", "text": visible})
        elif kind == "text_end":
            if buffered_answer:
                visible = "".join(buffered_answer)
                for source in sorted(set(sources), key=len, reverse=True):
                    visible = visible.replace(source, "[临时测试代码已隐藏]")
                buffered_answer = []
            else:
                visible = redactor.finish()
            if visible:
                send({"type": "answer_delta", "text": visible})
        elif kind == "text_discard":
            redactor = StreamRedactor(sources)
            buffered_answer = []
            send({"type": "answer_reset"})
        elif kind == "text_interrupted":
            redactor = StreamRedactor(sources)
            buffered_answer = []
            send({"type": "answer_interrupted"})
        elif kind in {"end", "interrupted"}:
            send({"type": "reasoning_end", "interrupted": kind == "interrupted"})
            if kind == "interrupted" and sections:
                sections.pop()

    def run() -> None:
        try:
            with reasoning_events(emit), tool_events(lambda kind, data: send({"type": "tool_" + kind, **data})), test_source_events(sources.append):
                result = _run_chat(request, sections, context_usage)
            send({"type": "final", **result})
        except HTTPException as exc:
            send({"type": "error", "message": str(exc.detail)})
        except Exception:
            send({"type": "error", "message": "请求失败，可能已执行部分工具，请先检查执行状态。"})
        finally:
            send({"type": "done"})

    def generate():
        threading.Thread(target=run, daemon=True).start()
        try:
            while True:
                event = events.get()
                if event["type"] == "done":
                    break
                yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
        finally:
            disconnected.set()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _run_chat(request: ChatRequest, reasoning: list[str] | None = None, context_usage: dict[str, Any] | None = None) -> dict[str, Any]:
    with session_operation(request.session_id):
        return _run_chat_inner(request, reasoning, context_usage)


def _run_chat_inner(request: ChatRequest, reasoning: list[str] | None = None, context_usage: dict[str, Any] | None = None) -> dict[str, Any]:
    session_id = session_store.normalize_id(request.session_id) if request.session_id else None
    if request.session_id and session_id is None:
        raise HTTPException(status_code=400, detail="会话标识无效。")
    try:
        if session_id:
            identity, store = locate_session(session_id)
            existing = store.get_session(session_id)
            if existing and request.workspace_id is not None and identity != request.workspace_id:
                raise HTTPException(status_code=409, detail="此会话属于其他工作区，请新建会话。")
            if existing is None:
                raise HTTPException(status_code=404, detail="会话不存在，请新建会话。")
        else:
            identity = request.workspace_id
            store = workspace_store(identity) if identity is not None else session_store
        history = store.load_context_projection(session_id).messages if session_id else []
    except SessionStoreError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    current_agent = selected_agent(identity)
    if not session_id:
        session_id = str(uuid.uuid4())
    old_length = len(history)

    try:
        turn = store.begin_turn(session_id, request.message)
    except SessionStoreError as exc:
        raise HTTPException(status_code=500, detail=f"无法记录轮次开始：{exc}") from exc

    def journal(kind: str, data: dict[str, Any]) -> None:
        store.append_turn_event(session_id, turn, kind, data)

    try:
        handler = ContextHandler(store, session_id, history, current_agent.model.summarize) if isinstance(current_agent, Agent) else None
        with read_session(session_id), model_context(handler), journal_events(journal), \
                email_evidence(lambda operation_id: store.email_tool_message(session_id, operation_id)):
            state = current_agent.run(request.message, history)
    except ContextCompactionError as exc:
        journal("turn/end", {"reason": "failed"})
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except APITimeoutError as exc:
        journal("turn/end", {"reason": "failed"})
        raise HTTPException(status_code=504, detail="模型服务请求超时。任务可能已执行部分工具，请先检查工作区和工具状态，再决定是否重试。") from exc
    except APIConnectionError as exc:
        journal("turn/end", {"reason": "failed"})
        raise HTTPException(status_code=502, detail="无法连接模型服务，请检查服务地址、网络及代理。任务可能已执行部分工具，请先检查工作区和工具状态，再决定是否重试。") from exc
    except Exception as exc:
        try:
            journal("turn/end", {"reason": "failed"})
        except SessionStoreError:
            pass
        raise HTTPException(status_code=502, detail=f"Agent 请求失败：{type(exc).__name__}") from exc

    messages = state["messages"]
    new_messages = messages[old_length:] if history else messages[1:]

    calls: dict[str, dict[str, Any]] = {}
    events: list[dict[str, Any]] = []
    for message in new_messages:
        for call in message.get("tool_calls", []):
            function = call.get("function", {})
            try:
                arguments = json.loads(function.get("arguments", "{}"))
            except (TypeError, json.JSONDecodeError):
                arguments = {}
            entry = {"name": function.get("name", "tool"), "arguments": arguments, "result": None}
            calls[call.get("id", "")] = entry
            events.append(entry)
        if message.get("role") == "tool":
            try:
                result = json.loads(message.get("content", "{}"))
            except json.JSONDecodeError:
                result = {"content": message.get("content", "")}
            call = calls.get(message.get("tool_call_id", ""))
            if call:
                call["result"] = result

    answer = next((message.get("content") for message in reversed(new_messages)
                   if message.get("role") == "assistant" and not message.get("tool_calls")), "")
    # Hide an exact reproduction of a temporary test in the user-facing answer;
    # the model still sees the full tool request and output in its context.
    test_sources = []
    for event in events:
        if event.get("name") == "run_inline_tests":
            arguments = event.get("arguments")
            source = arguments.get("tests") if isinstance(arguments, dict) else None
            if isinstance(source, str) and source.strip():
                test_sources.append(source.strip())
    for source in sorted(set(test_sources), key=len, reverse=True):
        if answer:
            answer = answer.replace(source, "[临时测试代码已隐藏]")
    visible_events = [display_tool_event(event) for event in events]
    agent_delta = messages if old_length == 0 else messages[old_length:]
    try:
        store.append_turn(session_id, agent_delta, request.message, answer or "", visible_events,
                                  [section for section in reasoning if section] if reasoning else None,
                                  context_usage or None)
        journal("turn/end", {"reason": state.get("stop_reason") or "completed"})
    except SessionStoreError as exc:
        raise HTTPException(status_code=500, detail=f"Agent 已执行，但会话记录未能持久化：{exc}") from exc
    return {"session_id": session_id, "answer": answer or "", "tools": visible_events,
            "process": [tool_process(event) for event in visible_events],
            "reasoning": [section for section in reasoning if section] if reasoning else [], "workspace_id": identity,
            "context_usage": context_usage or None}
