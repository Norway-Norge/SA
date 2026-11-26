import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Activity, ArrowUp, ChevronDown,
  Clock3, FileSearch, FileText, Folder, FolderPlus, Info, LockKeyhole, Menu,
  PanelRightClose, Pencil, Plus, RotateCcw, Search, Trash2,
  ShieldCheck, Sparkles, TerminalSquare, X,
} from "lucide-react";

type ToolEvent = {
  name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown> | null;
};
type ProcessItem = { name: string; path?: string; status: string };
type ContextUsage = { prompt_tokens: number; completion_tokens: number; model: string };
type LiveStep =
  | { kind: "reasoning"; text: string }
  | { kind: "tool"; callId: string; name: string; path?: string; status: string };

type ChatMessage = {
  role: "user" | "assistant";
  content: string;
  tools?: ToolEvent[];
  reasoning?: string[];
  process?: ProcessItem[];
  context_usage?: ContextUsage | null;
};

type Session = { id: string; title: string; serverId: string | null; messages: ChatMessage[]; workspaceId?: string | null; titleCustom?: boolean };
type RemoteSession = { session_id: string; title: string; updated_at: string; messages: ChatMessage[]; workspace_id?: string | null; deleted?: boolean };
type Workspace = { id: string; name: string; path: string };
type SkillCatalog = { skills: { name: string; description: string; source: string; path: string; conflict: boolean }[]; issues: { path: string; error: string }[]; truncated: boolean };
type Personas = { personas: string[]; selected: string | null };

function personaLabel(name: string) {
  if (name === "catgirl") return "可爱猫娘";
  if (name === "detective") return "工藤新一";
  if (name === "demon") return "恶魔小子唐喻琪";
  return name;
}

type Health = { ok: boolean; configured: boolean; workspace?: string | null; model?: string; tools?: string[]; context_window?: number | null };

type ConversationState = { version: 1; activeId: string; sessions: Session[] };

const conversationStorageKey = "secureagent.conversations.v1";

const makeSession = (workspaceId?: string | null): Session => ({
  id: crypto.randomUUID(), title: "新对话", serverId: null, messages: [], workspaceId,
});

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isContextUsage(value: unknown): value is ContextUsage {
  return isRecord(value) && typeof value.model === "string"
    && Number.isSafeInteger(value.prompt_tokens) && (value.prompt_tokens as number) >= 0
    && Number.isSafeInteger(value.completion_tokens) && (value.completion_tokens as number) >= 0;
}

function isChatMessage(value: unknown): value is ChatMessage {
  if (!isRecord(value) || (value.role !== "user" && value.role !== "assistant") || typeof value.content !== "string") {
    return false;
  }
  if (value.reasoning !== undefined && (!Array.isArray(value.reasoning) || !value.reasoning.every((item) => typeof item === "string"))) return false;
  if (value.process !== undefined && (!Array.isArray(value.process) || !value.process.every((item) => isRecord(item) && typeof item.name === "string" && typeof item.status === "string" && (item.path === undefined || typeof item.path === "string")))) return false;
  if (value.tools === undefined) return true;
  return Array.isArray(value.tools) && value.tools.every((tool) =>
    isRecord(tool)
    && typeof tool.name === "string"
    && isRecord(tool.arguments)
    && (tool.result === null || isRecord(tool.result))
  );
}

function isSession(value: unknown): value is Session {
  return isRecord(value)
    && typeof value.id === "string"
    && typeof value.title === "string"
    && (typeof value.serverId === "string" || value.serverId === null)
    && (value.workspaceId === undefined || value.workspaceId === null || typeof value.workspaceId === "string")
    && (value.titleCustom === undefined || typeof value.titleCustom === "boolean")
    && Array.isArray(value.messages)
    && value.messages.every(isChatMessage);
}

function loadConversationState(): ConversationState {
  try {
    const stored = localStorage.getItem(conversationStorageKey);
    if (stored) {
      const parsed: unknown = JSON.parse(stored);
      if (isRecord(parsed) && parsed.version === 1 && Array.isArray(parsed.sessions)) {
        const sessions = parsed.sessions.filter(isSession);
        if (sessions.length) {
          const requestedActiveId = typeof parsed.activeId === "string" ? parsed.activeId : "";
          return {
            version: 1,
            activeId: sessions.some((session) => session.id === requestedActiveId) ? requestedActiveId : sessions[0].id,
            sessions,
          };
        }
      }
    }
  } catch {
    // Ignore unavailable or malformed browser storage and start a fresh conversation.
  }
  const session = makeSession();
  return { version: 1, activeId: session.id, sessions: [session] };
}

function formatTool(name: string) {
  if (name === "read_file") return "读取文件";
  if (name === "search_files") return "搜索文件";
  if (name === "glob") return "列出工作区文件";
  if (name === "web_search") return "搜索互联网";
  if (name === "web_fetch") return "读取网页";
  if (name === "skill") return "加载 Skill";
  if (name === "read_spill") return "读取完整结果";
  if (name === "create_file") return "创建文件";
  if (name === "write_file") return "写入文件";
  if (name === "edit_file") return "编辑文件";
  if (name === "send_email") return "发送邮件";
  if (name === "run_tests") return "运行测试";
  if (name === "run_inline_tests") return "测试临时代码";
  if (name === "safe_delete") return "安全删除";
  if (name === "list_recycle_bin") return "查看回收区";
  if (name === "restore_file") return "恢复文件或目录";
  if (name === "convert_file") return "转换文件";
  return name;
}

function toolStatus(status: string) {
  if (status === "RUNNING") return "执行中";
  if (status === "REVIEW") return "等待审批";
  if (status === "UNKNOWN") return "结果未知";
  if (status === "PARTIAL") return "部分完成";
  if (status === "FAILED") return "执行失败或被拒绝";
  return status === "COMPLETED" ? "已完成" : "结果未知";
}

function TurnProcess({ items, running = false }: { items: ProcessItem[]; running?: boolean }) {
  if (!items.length) return null;
  return <details className="turn-process" open={running || undefined}>
    <summary>{running ? "正在处理" : "本轮过程"}<ChevronDown size={14} /></summary>
    <div className="turn-process-list">{items.map((item, index) => <div className="turn-process-item" key={index}>
      <TerminalSquare size={14} /><span>{formatTool(item.name)}</span>{item.path && <code>{item.path}</code>}
      {item.status !== "COMPLETED" && <span className="turn-process-status">{toolStatus(item.status)}</span>}
    </div>)}</div>
  </details>;
}

function DirectoryPreview({ entries, count, totalBytes }: { entries: unknown[]; count: unknown; totalBytes: unknown }) {
  const itemCount = typeof count === "number" && Number.isSafeInteger(count) && count >= 0 ? count : entries.length;
  const size = typeof totalBytes === "number" && Number.isSafeInteger(totalBytes) && totalBytes >= 0 ? `，共 ${totalBytes.toLocaleString("zh-CN")} 字节` : "";
  return <div className="directory-preview">
    <div className="directory-preview-heading">目录内容：{itemCount.toLocaleString("zh-CN")} 项{size}</div>
    {entries.length === 0 ? <p className="directory-empty">空目录</p> : <ul className="directory-entries" tabIndex={0} aria-label="目录内容清单">
      {entries.map((entry, index) => {
        const item = isRecord(entry) ? entry : {};
        const isDirectory = item.type === "directory";
        const isFile = item.type === "file";
        const path = typeof item.path === "string" && item.path ? item.path : "无法显示项目路径";
        const itemSize = isFile && typeof item.size === "number" && Number.isSafeInteger(item.size) && item.size >= 0 ? `${item.size.toLocaleString("zh-CN")} 字节` : "";
        return <li className="directory-entry" key={index}>
          {isDirectory ? <Folder size={15} aria-hidden="true" /> : <FileText size={15} aria-hidden="true" />}
          <span className="directory-entry-path">{path}</span>
          <span className="directory-entry-meta">{isDirectory ? "文件夹" : isFile ? "文件" : "未知类型"}{itemSize && ` · ${itemSize}`}</span>
        </li>;
      })}
    </ul>}
  </div>;
}

function App() {
  const [initialConversation] = useState(loadConversationState);
  const [sessions, setSessions] = useState<Session[]>(initialConversation.sessions);
  const [activeId, setActiveId] = useState(initialConversation.activeId);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState("");
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [selectedWorkspace, setSelectedWorkspace] = useState<string | null>(null);
  const [pickingWorkspace, setPickingWorkspace] = useState(false);
  const [workspacesReady, setWorkspacesReady] = useState(false);
  const [workspaceRevision, setWorkspaceRevision] = useState(0);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [sessionAction, setSessionAction] = useState(false);
  const [showDeleted, setShowDeleted] = useState(false);
  const [deletedSessions, setDeletedSessions] = useState<Omit<RemoteSession, "messages">[]>([]);
  const [deletedLocal, setDeletedLocal] = useState<Session[]>([]);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [skillCatalog, setSkillCatalog] = useState<SkillCatalog | null>(null);
  const [skillError, setSkillError] = useState("");
  const [skillRevision, setSkillRevision] = useState(0);
  const [personas, setPersonas] = useState<Personas | null>(null);
  const [personaError, setPersonaError] = useState("");
  const [personaSaving, setPersonaSaving] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [notice, setNotice] = useState("");
  const [liveReasoning, setLiveReasoning] = useState<{ sessionId: string; steps: LiveStep[]; answer: string; answerInterrupted: boolean } | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const feedRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);

  useEffect(() => {
    try {
      localStorage.setItem(conversationStorageKey, JSON.stringify({ version: 1, activeId, sessions }));
    } catch {
      setNotice("浏览器本地存储不可用或空间不足；本次对话暂未持久化。");
    }
  }, [activeId, sessions]);

  useEffect(() => {
    fetch("/api/health")
      .then(async (response) => {
        if (!response.ok) throw new Error("后端服务暂不可用");
        setHealth(await response.json());
      })
      .catch((error: Error) => setHealthError(error.message));
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/workspaces").then(async (response) => {
      if (!response.ok) throw new Error("无法加载工作区列表");
      return await response.json() as { workspaces: Workspace[] };
    }).then((data) => {
      if (cancelled) return;
      setWorkspaces(data.workspaces);
      const initial = initialConversation.sessions.find((session) => session.id === initialConversation.activeId);
      const preferred = initial?.workspaceId || data.workspaces[0]?.id || null;
      setSelectedWorkspace((current) => current || preferred);
      if (preferred && initial && !initial.serverId && !initial.messages.length && initial.workspaceId === undefined) {
        setSessions((current) => current.map((session) => session.id === initial.id && !session.serverId && !session.messages.length && session.workspaceId === undefined ? { ...session, workspaceId: preferred } : session));
      }
    }).catch((error: Error) => { if (!cancelled) setNotice(error.message); }).finally(() => { if (!cancelled) setWorkspacesReady(true); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!workspacesReady) return;
    let cancelled = false;
    setLoadingSessions(true);
    fetch("/api/sessions?include_deleted=true")
      .then(async (response) => {
        if (!response.ok) throw new Error("无法加载已保存的会话");
        const data: { sessions: Omit<RemoteSession, "messages">[] } = await response.json();
        const deleted = data.sessions.filter((session) => session.deleted);
        const restored = await Promise.all(data.sessions.filter((session) => !session.deleted).map(async (session) => {
          const detailResponse = await fetch(`/api/sessions/${encodeURIComponent(session.session_id)}`);
          if (!detailResponse.ok) return null;
          return await detailResponse.json() as RemoteSession;
        }));
        return { restored, deleted };
      })
      .then(({ restored: remoteSessions, deleted }) => {
        if (cancelled) return;
        setDeletedSessions(deleted);
        const validSessions = remoteSessions.filter((session): session is RemoteSession => session !== null);
        setSessions((current) => {
          const restored = validSessions.map((remote) => {
            const existing = current.find((session) => session.serverId === remote.session_id);
            return {
              id: existing?.id ?? remote.session_id,
              title: remote.title,
              serverId: remote.session_id,
              workspaceId: remote.workspace_id ?? null,
              messages: remote.messages,
            };
          });
          const knownServerIds = new Set(validSessions.map((session) => session.session_id));
          const deletedIds = new Set(deleted.map((session) => session.session_id));
          const next = [...restored, ...current.filter((session) => !session.serverId || (!knownServerIds.has(session.serverId) && !deletedIds.has(session.serverId)))];
          return next.length ? next : [makeSession(workspaces[0]?.id || null)];
        });
      })
      .catch(() => { if (!cancelled) setNotice("无法同步会话列表，请检查后端连接。"); })
      .finally(() => { if (!cancelled) setLoadingSessions(false); });
    return () => { cancelled = true; };
  }, [workspacesReady, workspaceRevision]);

  const active = useMemo(() => sessions.find((session) => session.id === activeId) ?? sessions[0], [sessions, activeId]);
  const latestAssistant = [...(active?.messages ?? [])].reverse().find((message) => message.role === "assistant");
  const usage = isContextUsage(latestAssistant?.context_usage) ? latestAssistant.context_usage : null;
  const used = usage ? usage.prompt_tokens + usage.completion_tokens : null;
  const windowSize = usage?.model === health?.model ? health?.context_window : null;
  const usagePercent = used !== null && windowSize && windowSize > 0 ? used / windowSize * 100 : null;
  const usageBusy = busy && active?.id === liveReasoning?.sessionId;
  const usageLabel = usageBusy ? "上下文用量：本轮生成中" : used === null ? "上下文用量：暂无数据" : windowSize && usage
    ? `上次模型调用占上下文窗口 ${usagePercent!.toFixed(1)}%；输入 ${usage.prompt_tokens.toLocaleString()} tokens，输出 ${usage.completion_tokens.toLocaleString()} tokens，窗口 ${windowSize.toLocaleString()} tokens。下一次请求可能变化。`
    : `上次模型调用 ${used.toLocaleString()} tokens；当前模型窗口未配置或与记录不一致。`;
  const activeWorkspace = workspaces.find((workspace) => workspace.id === active?.workspaceId);
  const personaWorkspace = active?.workspaceId ?? null;
  const personaAvailable = Boolean(active?.workspaceId || active?.serverId);
  const personaScopeRef = useRef(personaWorkspace);
  personaScopeRef.current = personaWorkspace;

  useEffect(() => {
    setPersonas(null);
    setPersonaError("");
    if (!personaAvailable) return;
    const controller = new AbortController();
    const query = personaWorkspace ? `?workspace_id=${encodeURIComponent(personaWorkspace)}` : "";
    fetch(`/api/personas${query}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("无法读取人格设置，请检查工作区和后端配置。");
        return await response.json() as Personas;
      })
      .then((data) => setPersonas(data))
      .catch((error: Error) => { if (error.name !== "AbortError") setPersonaError(error.message); });
    return () => controller.abort();
  }, [personaWorkspace, personaAvailable]);

  async function changePersona(name: string) {
    const requestedWorkspace = personaWorkspace;
    setPersonaSaving(true);
    setPersonaError("");
    try {
      const response = await fetch("/api/personas", {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name || null, workspace_id: requestedWorkspace }),
      });
      if (!response.ok) throw new Error("无法切换人格，请刷新后重试。");
      const data = await response.json() as Personas;
      if (personaScopeRef.current === requestedWorkspace) setPersonas(data);
    } catch (error) {
      if (personaScopeRef.current === requestedWorkspace) {
        const message = error instanceof Error ? error.message : "无法切换人格。";
        setPersonaError(message);
        setNotice(message);
      }
    } finally {
      setPersonaSaving(false);
    }
  }

  useEffect(() => {
    if (!inspectorOpen || !activeWorkspace) { setSkillCatalog(null); setSkillError(""); return; }
    const controller = new AbortController();
    setSkillCatalog(null);
    setSkillError("");
    fetch(`/api/workspaces/${encodeURIComponent(activeWorkspace.id)}/skills`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("无法读取本地 Skill，请检查后端是否已更新及工作区是否可用。");
        return await response.json() as SkillCatalog;
      })
      .then((data) => setSkillCatalog(data))
      .catch((error: Error) => { if (error.name !== "AbortError") setSkillError(error.message); });
    return () => controller.abort();
  }, [inspectorOpen, activeWorkspace?.id, skillRevision]);

  useEffect(() => {
    if (sessions.length && !sessions.some((session) => session.id === activeId)) setActiveId(sessions[0].id);
  }, [sessions, activeId]);

  useEffect(() => {
    if (!active?.serverId) return;
    let cancelled = false;
    const pending = active.messages.flatMap((message) => message.tools || [])
      .filter((tool) => ["edit_file", "safe_delete", "restore_file", "convert_file", "send_email"].includes(tool.name) && tool.result?.status === "REVIEW" && typeof tool.result.approval_id === "string");
    if (!pending.length) return;
    Promise.all(pending.map(async (tool) => {
      const id = String(tool.result?.approval_id);
      const group = tool.name === "send_email" ? "email-approvals" : tool.name === "edit_file" ? "edit-approvals" : "recycle-approvals";
      const response = await fetch(`/api/${group}/${encodeURIComponent(id)}?session_id=${encodeURIComponent(active.serverId!)}`);
      if (!response.ok) return null;
      return { id, result: await response.json() as Record<string, unknown> };
    })).then((statuses) => {
      if (cancelled) return;
      const changed = statuses.filter((item) => item && item.result.status !== "REVIEW");
      if (!changed.length) return;
      setSessions((current) => current.map((session) => session.id !== active.id ? session : {
        ...session,
        messages: session.messages.map((message) => ({ ...message, tools: message.tools?.map((tool) => {
          const match = changed.find((item) => item?.id === tool.result?.approval_id);
          return match ? { ...tool, result: { ...match.result, approval_id: match.id } } : tool;
        }) })),
      }));
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [active?.id, active?.serverId, active?.messages]);

  useEffect(() => {
    stickToBottom.current = true;
    feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight });
  }, [active?.id]);

  useEffect(() => {
    if (stickToBottom.current) feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight });
  }, [active?.messages, busy, liveReasoning]);

  function startSession(workspaceId = selectedWorkspace) {
    const session = makeSession(workspaceId);
    setSessions((current) => [session, ...current]);
    setActiveId(session.id);
    setDraft("");
    setSidebarOpen(false);
    textareaRef.current?.focus();
  }

  async function addWorkspace() {
    if (pickingWorkspace || busy || sessionAction || loadingSessions) return;
    setPickingWorkspace(true);
    setNotice("");
    try {
      const response = await fetch("/api/workspaces/pick", { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "无法添加工作区");
      if (data.cancelled) return;
      const workspace = data.workspace as Workspace;
      setWorkspaces((current) => [...current.filter((item) => item.id !== workspace.id), workspace]);
      setSelectedWorkspace(workspace.id);
      startSession(workspace.id);
      setWorkspaceRevision((value) => value + 1);
      const healthResponse = await fetch("/api/health");
      if (healthResponse.ok) setHealth(await healthResponse.json());
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法添加工作区");
    } finally {
      setPickingWorkspace(false);
    }
  }

  async function renameSession(session: Session) {
    if (busy || sessionAction || loadingSessions) return;
    const entered = window.prompt("修改会话标题", session.title);
    if (entered === null) return;
    const title = entered.trim();
    if (!title || title.length > 80 || /[\x00-\x1f]/.test(title)) {
      setNotice("标题需为 1 至 80 个字符，且不能包含换行或控制字符。");
      return;
    }
    if (title === session.title) return;
    setSessionAction(true);
    setNotice("");
    try {
      if (session.serverId) {
        const response = await fetch(`/api/sessions/${encodeURIComponent(session.serverId)}`, {
          method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }),
        });
        if (!response.ok) {
          const result = await response.json();
          throw new Error(result.detail || "无法修改会话标题");
        }
      }
      setSessions((current) => current.map((item) => item.id === session.id ? { ...item, title, titleCustom: !item.serverId } : item));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法修改会话标题");
    } finally {
      setSessionAction(false);
    }
  }

  async function deleteSession(session: Session) {
    if (busy || sessionAction || loadingSessions) return;
    setSessionAction(true);
    setNotice("");
    try {
      if (session.serverId) {
        const response = await fetch(`/api/sessions/${encodeURIComponent(session.serverId)}`, { method: "DELETE" });
        if (!response.ok) {
          const result = await response.json();
          throw new Error(result.detail || "无法删除会话");
        }
        setDeletedSessions((current) => [{ session_id: session.serverId!, title: session.title, workspace_id: session.workspaceId, updated_at: "", deleted: true }, ...current.filter((item) => item.session_id !== session.serverId)]);
      } else {
        setDeletedLocal((current) => [session, ...current]);
      }
      const remaining = sessions.filter((item) => item.id !== session.id);
      if (activeId === session.id) {
        const next = remaining.find((item) => item.workspaceId === session.workspaceId) || remaining[0] || makeSession(session.workspaceId ?? selectedWorkspace);
        if (!remaining.some((item) => item.id === next.id)) remaining.unshift(next);
        setActiveId(next.id);
        setSelectedWorkspace(next.workspaceId ?? selectedWorkspace);
        setLiveReasoning(null);
      }
      setSessions(remaining);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法删除会话");
    } finally {
      setSessionAction(false);
    }
  }

  async function toggleDeleted() {
    if (showDeleted) { setShowDeleted(false); return; }
    setShowDeleted(true);
    try {
      const response = await fetch("/api/sessions?include_deleted=true");
      if (!response.ok) throw new Error("无法读取已删除会话");
      const data: { sessions: Omit<RemoteSession, "messages">[] } = await response.json();
      setDeletedSessions(data.sessions.filter((session) => session.deleted));
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法读取已删除会话");
    }
  }

  async function restoreSession(session: Omit<RemoteSession, "messages">) {
    if (busy || sessionAction || loadingSessions) return;
    setSessionAction(true);
    setNotice("");
    try {
      const response = await fetch(`/api/sessions/${encodeURIComponent(session.session_id)}/restore`, { method: "POST" });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.detail || "无法恢复会话");
      }
      setDeletedSessions((current) => current.filter((item) => item.session_id !== session.session_id));
      setWorkspaceRevision((value) => value + 1);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "无法恢复会话");
    } finally {
      setSessionAction(false);
    }
  }

  async function resetSandbox() {
    if (!active?.serverId || busy) return;
    if (!window.confirm("重建将清除当前会话测试沙箱中的所有状态。下次测试会复制最新工作区。继续吗？")) return;
    setBusy(true);
    try {
      const response = await fetch("/api/sandbox/reset", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: active.serverId }),
      });
      const result = await response.json();
      if (!response.ok || result.status !== "RESET") throw new Error(result.detail || result.error || "重建失败");
      setNotice(result.warning || result.message);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "重建测试沙箱失败");
    } finally {
      setBusy(false);
    }
  }

  async function sendMessage(value = draft) {
    const message = value.trim();
    if (!message || !active || busy) return;
    if (loadingSessions) { setNotice("正在加载会话，请稍后再发送消息。"); return; }
    if (!active.serverId && !active.workspaceId) {
      setNotice("请先添加或选择工作区，再发送消息。");
      return;
    }
    const currentId = active.id;
    stickToBottom.current = true;
    setDraft("");
    setBusy(true);
    setNotice("");
    setLiveReasoning({ sessionId: currentId, steps: [], answer: "", answerInterrupted: false });
    setSessions((current) => current.map((session) => session.id === currentId
      ? { ...session, title: session.messages.length || session.titleCustom ? session.title : message.slice(0, 26), messages: [...session.messages, { role: "user", content: message }] }
      : session));
    let completedNormally = false;
    try {
      const response = await fetch("/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: active.serverId, workspace_id: active.workspaceId ?? null }),
      });
      if (!response.ok) {
        if (response.status === 404) throw new Error("后端尚未加载流式对话接口，请重启 SA 后端后重试。");
        let detail = "";
        try {
          const errorData: unknown = await response.json();
          if (isRecord(errorData) && typeof errorData.detail === "string") detail = errorData.detail;
        } catch {
          // A proxy or older server may return a non-JSON error response.
        }
        throw new Error(detail || `请求失败（HTTP ${response.status}），请检查后端服务。`);
      }
      if (!response.body) throw new Error("后端未返回对话数据，请检查服务状态。");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let pending = "";
      let data: { session_id: string; answer: string; tools: ToolEvent[]; reasoning?: string[]; process?: ProcessItem[]; workspace_id?: string | null; context_usage?: ContextUsage | null } | undefined;
      while (true) {
        const chunk = await reader.read();
        pending += decoder.decode(chunk.value, { stream: !chunk.done }).replace(/\r\n/g, "\n");
        const frames = pending.split("\n\n");
        pending = frames.pop() || "";
        for (const frame of frames) {
          const line = frame.split("\n").find((item) => item.startsWith("data: "));
          if (!line) continue;
          const event = JSON.parse(line.slice(6));
          if (event.type === "error") throw new Error(event.message || "Agent 请求失败");
          if (event.type === "reasoning_start") setLiveReasoning((current) => current?.sessionId === currentId ? { ...current, answer: current.answerInterrupted ? "" : current.answer, answerInterrupted: false, steps: [...current.steps, { kind: "reasoning", text: "" }] } : current);
          if (event.type === "reasoning_delta" && typeof event.text === "string") setLiveReasoning((current) => {
            if (!current || current.sessionId !== currentId || !current.steps.length) return current;
            const steps = [...current.steps];
            const last = steps[steps.length - 1];
            if (last.kind !== "reasoning") return current;
            steps[steps.length - 1] = { ...last, text: last.text + event.text };
            return { ...current, steps };
          });
          if (event.type === "reasoning_end" && event.interrupted) setLiveReasoning((current) => current?.sessionId === currentId && current.steps.at(-1)?.kind === "reasoning" ? { ...current, steps: current.steps.slice(0, -1) } : current);
          if (event.type === "answer_delta" && typeof event.text === "string") setLiveReasoning((current) => current?.sessionId === currentId ? { ...current, answer: current.answer + event.text } : current);
          if (event.type === "answer_reset") setLiveReasoning((current) => current?.sessionId === currentId ? { ...current, answer: "", answerInterrupted: false } : current);
          if (event.type === "answer_interrupted") setLiveReasoning((current) => current?.sessionId === currentId ? { ...current, answerInterrupted: true } : current);
          if (event.type === "tool_start" && typeof event.call_id === "string" && typeof event.name === "string") setLiveReasoning((current) => current?.sessionId === currentId ? { ...current, steps: [...current.steps, { kind: "tool", callId: event.call_id, name: event.name, path: typeof event.path === "string" ? event.path : undefined, status: "RUNNING" }] } : current);
          if (event.type === "tool_result" && typeof event.call_id === "string" && typeof event.status === "string") setLiveReasoning((current) => {
            if (!current || current.sessionId !== currentId) return current;
            const steps = [...current.steps];
            for (let index = steps.length - 1; index >= 0; index--) {
              const step = steps[index];
              if (step.kind === "tool" && step.callId === event.call_id && step.status === "RUNNING") {
                steps[index] = { ...step, status: event.status };
                break;
              }
            }
            return { ...current, steps };
          });
          if (event.type === "final") data = event;
        }
        if (chunk.done) break;
      }
      if (!data) throw new Error("连接中断，任务可能已执行部分工具，请先检查状态。");
      const completed = data;
      if (!active.serverId && active.titleCustom) {
        try {
          const titleResponse = await fetch(`/api/sessions/${encodeURIComponent(completed.session_id)}`, {
            method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: active.title }),
          });
          if (!titleResponse.ok) throw new Error("标题未能保存，请重试修改标题。");
        } catch {
          setNotice("对话已完成，但标题未能确认保存；请重新修改标题。");
        }
      }
      setSessions((current) => current.map((session) => session.id === currentId
        ? { ...session, serverId: completed.session_id, workspaceId: completed.workspace_id ?? session.workspaceId ?? null, messages: [...session.messages, { role: "assistant", content: completed.answer, tools: completed.tools, reasoning: completed.reasoning, process: completed.process, context_usage: completed.context_usage }] }
        : session));
      completedNormally = true;
    } catch (error) {
      setLiveReasoning((current) => current?.sessionId === currentId ? {
        ...current, answerInterrupted: !!current.answer, steps: current.steps.map((step) => step.kind === "tool" && step.status === "RUNNING" ? { ...step, status: "UNKNOWN" } : step),
      } : current);
      setNotice(error instanceof Error ? error.message : "Agent 请求失败");
    } finally {
      setBusy(false);
      if (completedNormally) setLiveReasoning(null);
      textareaRef.current?.focus();
    }
  }

  async function decideEdit(approvalId: string, approve: boolean, recycle = false, email = false) {
    if (!active?.serverId || busy) return;
    const currentId = active.id;
    setBusy(true);
    setNotice("");
    try {
      const response = await fetch(`/api/${email ? "email-approvals" : recycle ? "recycle-approvals" : "edit-approvals"}/${encodeURIComponent(approvalId)}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: active.serverId, approve }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || "审批请求失败");
      setSessions((current) => current.map((session) => session.id !== currentId ? session : {
        ...session,
        messages: [...session.messages.map((message) => ({ ...message,
          tools: message.tools?.map((tool) => tool.result?.approval_id === approvalId
            ? { ...tool, result: { ...result, approval_id: approvalId } } : tool),
        })), ...(result.code === "INVALID_APPROVAL" ? [] : [
          { role: "assistant" as const, content: typeof result.display_message === "string" ? result.display_message : "操作结果暂时无法确认，请先检查文件状态。" },
        ])],
      }));
      if (result.warning || result.code === "INVALID_APPROVAL") setNotice(result.warning || result.error || "审批不可用，请先检查状态。");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "审批请求失败，请先检查文件状态。");
    } finally {
      setBusy(false);
    }
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  const toolList = [
    { name: "read_file", label: "读取文件", detail: "授权工作区内文本文件", icon: FileSearch },
    { name: "search_files", label: "搜索文件", detail: "限定目录内文本搜索", icon: Search },
    { name: "glob", label: "列出文件", detail: "按路径模式枚举工作区文件", icon: Folder },
    { name: "web_search", label: "搜索互联网", detail: "通过 DeepSeek 搜索来源和摘要", icon: Search },
    { name: "web_fetch", label: "读取网页", detail: "通过 DeepSeek 读取公开网页", icon: FileText },
    { name: "skill", label: "加载 Skill", detail: "按需读取本地 Skill 正文", icon: FileText },
    { name: "read_spill", label: "读取完整结果", detail: "分页读取当前会话的大型工具结果", icon: FileText },
    { name: "create_file", label: "创建文件", detail: "创建工作区中的新文件", icon: FileSearch },
    { name: "write_file", label: "写入文件", detail: "先读取后写入已有空文件", icon: FileSearch },
    { name: "edit_file", label: "编辑文件", detail: "精确替换 · 需要审批", icon: FileSearch },
    { name: "safe_delete", label: "安全删除", detail: "文件或目录 · 需要审批", icon: FileSearch },
    { name: "list_recycle_bin", label: "查看回收区", detail: "列出本会话文件", icon: FileSearch },
    { name: "restore_file", label: "恢复文件或目录", detail: "恢复原路径 · 需要审批", icon: FileSearch },
    { name: "convert_file", label: "转换文件", detail: "新建目标并回收原文件 · 需要审批", icon: FileSearch },
    { name: "run_tests", label: "运行测试", detail: "Docker 隔离 · 可选启用", icon: TerminalSquare },
    { name: "run_inline_tests", label: "测试临时代码", detail: "一次性容器 · 不写入工作区", icon: TerminalSquare },
  ];

  function sessionRow(session: Session) {
    return <div key={session.id} className={`session-item workspace-session session-row ${session.id === activeId ? "selected" : ""}`}>
      <button className="session-open" disabled={sessionAction} onClick={() => { setSelectedWorkspace(session.workspaceId || workspaces[0]?.id || null); setActiveId(session.id); setSidebarOpen(false); }} title={session.title}><span>{session.title}</span></button>
      <div className="session-actions">
        <button onClick={() => void renameSession(session)} disabled={busy || sessionAction || loadingSessions} aria-label={`修改标题：${session.title}`} title="修改标题"><Pencil size={14} /></button>
        <button onClick={() => void deleteSession(session)} disabled={busy || sessionAction || loadingSessions} aria-label={`删除会话：${session.title}`} title="删除会话"><Trash2 size={14} /></button>
      </div>
    </div>;
  }

  return (
    <div className="app-shell">
      <aside className={`session-sidebar ${sidebarOpen ? "session-sidebar-open" : ""}`}>
        <div className="sidebar-heading">
          <div className="wordmark">SecureAgent <span>HARNESS</span></div>
          <button className="icon-button mobile-close" onClick={() => setSidebarOpen(false)} aria-label="关闭导航"><X size={17} /></button>
        </div>
        <button className="new-chat" disabled={sessionAction} onClick={() => startSession()}><Plus size={17} />新建对话</button>
        <div className="side-label workspace-heading"><span>工作区</span><button className="icon-button" onClick={() => void addWorkspace()} disabled={pickingWorkspace || busy || sessionAction || loadingSessions} aria-label="添加工作区" title="添加工作区"><FolderPlus size={17} /></button></div>
        <div className="session-list" tabIndex={0} aria-label="最近对话列表">
          {workspaces.map((workspace) => <div className="workspace-group" key={workspace.id}>
            <button className={`workspace-row ${active?.workspaceId === workspace.id ? "workspace-row-active" : ""}`} disabled={sessionAction} title={workspace.path} onClick={() => { setSelectedWorkspace(workspace.id); const blank = sessions.find((session) => session.workspaceId === workspace.id && !session.serverId && !session.messages.length); if (blank) { setActiveId(blank.id); setSidebarOpen(false); } else startSession(workspace.id); }}><Folder size={17} /><span>{workspace.name}</span></button>
            {sessions.filter((session) => session.workspaceId === workspace.id).map(sessionRow)}
          </div>)}
          {sessions.some((session) => !session.workspaceId) && <div className="workspace-group"><div className="legacy-label">未分组会话</div>{sessions.filter((session) => !session.workspaceId).map(sessionRow)}</div>}
          {!workspaces.length && <div className="workspace-empty">添加工作区后即可选择本地文件夹。</div>}
          <div className="deleted-sessions"><button className="deleted-toggle" onClick={() => void toggleDeleted()} aria-expanded={showDeleted}><Trash2 size={14} />已删除会话 <ChevronDown size={14} /></button>
            {showDeleted && <div className="deleted-list">
              {deletedSessions.map((session) => <div className="deleted-row" key={session.session_id}><div className="deleted-copy"><span title={session.title}>{session.title}</span><small>{workspaces.find((workspace) => workspace.id === session.workspace_id)?.name || "未分组"}</small></div><button disabled={busy || sessionAction || loadingSessions} onClick={() => void restoreSession(session)} aria-label={`恢复会话：${session.title}`} title="恢复会话"><RotateCcw size={14} /></button></div>)}
              {deletedLocal.map((session) => <div className="deleted-row" key={session.id}><div className="deleted-copy"><span title={session.title}>{session.title}</span><small>{workspaces.find((workspace) => workspace.id === session.workspaceId)?.name || "未分组"}</small></div><button disabled={busy || sessionAction || loadingSessions} onClick={() => { setSessions((current) => [session, ...current]); setDeletedLocal((current) => current.filter((item) => item.id !== session.id)); }} aria-label={`恢复会话：${session.title}`} title="恢复会话"><RotateCcw size={14} /></button></div>)}
              {!deletedSessions.length && !deletedLocal.length && <div className="workspace-empty">没有已删除会话</div>}
            </div>}
          </div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <button className="icon-button mobile-menu" onClick={() => setSidebarOpen((value) => !value)} aria-label="打开导航"><Menu size={19} /></button>
          <div className="crumbs"><strong>{active?.title || "新对话"}</strong></div>
          <div className="topbar-actions">
            <label className="persona-select" title={personaError || `${personaWorkspace ? "同一工作区的会话" : "未分组会话"}共享此设置，重启后恢复配置默认值`}>
              <span>人格（{personaWorkspace ? "工作区" : "未分组会话"}共享）</span>
              <select aria-label={`回复人格（${personaWorkspace ? "工作区" : "未分组会话"}共享）`} value={personas?.selected ?? ""}
                disabled={!personas || !personaAvailable || personaSaving || busy || loadingSessions}
                onChange={(event) => void changePersona(event.target.value)}>
                {!personas && <option value="">{personaError ? "加载失败" : "加载中"}</option>}
                {personas && <option value="">不使用人格</option>}
                {personas?.personas.map((name) => <option value={name} key={name}>{personaLabel(name)}</option>)}
              </select>
            </label>
            <div className={`connection-pill ${healthError ? "offline" : health?.configured ? "online" : "pending"}`}><span />{healthError ? "未连接" : health?.configured ? "配置就绪" : "待配置"}</div>
            <button className="topbar-button" onClick={() => setInspectorOpen((value) => !value)} aria-expanded={inspectorOpen}><PanelRightClose size={17} /> <span>运行详情</span></button>
          </div>
        </header>

        <div className="work-area">
          <section className="conversation">
            <div className="message-feed" ref={feedRef} tabIndex={0} aria-label="会话消息" onScroll={(event) => {
              const area = event.currentTarget;
              stickToBottom.current = area.scrollHeight - area.scrollTop - area.clientHeight < 80;
            }}>
              {active?.messages.length === 0 ? (
                <div className="welcome">
                  <div className="welcome-eyebrow"><span className="welcome-line" />LOCAL AGENT WORKSPACE</div>
                  <h1>你好，今天<br />想一起完成什么？</h1>
                  <p>描述一个任务，SecureAgent 会在授权工作区内分析内容、调用工具，并展示每一步执行情况。</p>
                  <div className="suggestion-grid">
                    <button onClick={() => setDraft("帮我总结当前工作区的项目结构") }><div className="suggestion-icon blue"><Folder size={17} /></div><span><strong>了解项目结构</strong><small>总结工作区目录和主要文件</small></span><ArrowUp size={14} /></button>
                    <button onClick={() => setDraft("搜索工作区中与安全策略相关的实现") }><div className="suggestion-icon violet"><Search size={17} /></div><span><strong>搜索代码</strong><small>查找实现、配置或关键字</small></span><ArrowUp size={14} /></button>
                    <button onClick={() => setDraft("读取 README.md 并概括项目用法") }><div className="suggestion-icon amber"><FileSearch size={17} /></div><span><strong>阅读文档</strong><small>在授权目录内读取文本文件</small></span><ArrowUp size={14} /></button>
                    <button onClick={() => setDraft("运行当前项目的测试并总结结果") }><div className="suggestion-icon green"><TerminalSquare size={17} /></div><span><strong>运行测试</strong><small>仅在 Docker 沙箱可用时执行</small></span><ArrowUp size={14} /></button>
                  </div>
                </div>
              ) : (
                <div className="messages">
                  {active?.messages.map((message, index) => (
                    <article key={`${active.id}-${index}`} className={`message-row ${message.role}`}>
                      <div className="message-body">
                        {message.role === "assistant" && message.reasoning?.filter(Boolean).map((text, reasoningIndex) => <details className="reasoning-row" key={reasoningIndex}><summary>思考过程 <ChevronDown size={14} /></summary><div className="reasoning-content"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt || "图片"}</span> }}>{text}</ReactMarkdown></div></details>)}
                        {message.role === "assistant" && <TurnProcess items={message.process ?? []} />}
                        <div className={`message-bubble ${message.role}`}>{message.role === "assistant" ? <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{
                          a: ({ children, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer">{children}</a>,
                          img: ({ alt }) => <span>{alt || "图片"}</span>,
                          table: ({ children }) => <div className="markdown-table-wrap"><table>{children}</table></div>,
                        }}>{message.content}</ReactMarkdown> : message.content}</div>
                        {message.tools?.filter((tool) => ["edit_file", "safe_delete", "restore_file", "convert_file", "send_email"].includes(tool.name) && tool.result?.status === "REVIEW" && typeof tool.result.approval_id === "string").map((tool, toolIndex) => (
                          <div key={`${tool.name}-${toolIndex}`}>
                          {tool.name === "edit_file" && tool.result?.status === "REVIEW" && typeof tool.result.approval_id === "string" && (
                            <div className="approval-card">
                              <div className="approval-heading"><span />等待审批</div>
                              <div className="approval-body"><p>允许本次编辑文件 <strong>{String(tool.arguments.path ?? "")}</strong>？</p>
                              <div className="approval-detail"><div><span>原文</span><pre>{String(tool.arguments.old_string ?? "")}</pre></div>
                              <div><span>替换为</span><pre>{String(tool.arguments.new_string ?? "")}</pre></div></div>
                              <div className="approval-note">{tool.arguments.replace_all === true ? "替换全部匹配" : "仅替换唯一匹配"}</div>
                              <div className="approval-actions"><button disabled={busy} onClick={() => void decideEdit(String(tool.result?.approval_id), false)}>拒绝</button>
                              <button className="approval-allow" disabled={busy} onClick={() => void decideEdit(String(tool.result?.approval_id), true)}>允许一次</button></div></div>
                            </div>
                          )}
                          {(["safe_delete", "restore_file"].includes(tool.name)) && tool.result?.status === "REVIEW" && typeof tool.result.approval_id === "string" && (
                            <div className="approval-card">
                              <div className="approval-heading"><span />等待审批</div>
                              <div className="approval-body"><p>允许本次{tool.name === "safe_delete" ? "移入回收区" : "恢复"}{tool.result.action === "delete_directory" || tool.result.action === "restore_directory" ? "整个目录" : "文件"} <strong>{String(tool.result.path ?? tool.arguments.path ?? "")}</strong>？</p>
                              {Array.isArray(tool.result.entries) && <DirectoryPreview entries={tool.result.entries} count={tool.result.entry_count} totalBytes={tool.result.total_bytes} />}
                              <div className="approval-actions"><button disabled={busy} onClick={() => void decideEdit(String(tool.result?.approval_id), false, true)}>拒绝</button>
                              <button className="approval-allow" disabled={busy} onClick={() => void decideEdit(String(tool.result?.approval_id), true, true)}>允许一次</button></div></div>
                            </div>
                          )}
                          {tool.name === "send_email" && tool.result?.status === "REVIEW" && typeof tool.result.approval_id === "string" && (
                            <div className="approval-card"><div className="approval-heading"><span />等待邮件发送审批</div>
                              <div className="approval-body"><div className="approval-detail">
                                <div><span>发件人</span><pre>{String(tool.result.from ?? "")}</pre></div>
                                <div><span>收件人</span><pre>{String(tool.result.to ?? "")}</pre></div>
                                <div><span>主题</span><pre>{String(tool.result.subject ?? "")}</pre></div>
                                <div><span>正文</span><pre>{String(tool.result.body ?? "")}</pre></div>
                              </div><div className="approval-actions">
                                <button disabled={busy} onClick={() => void decideEdit(String(tool.result?.approval_id), false, false, true)}>拒绝</button>
                                <button className="approval-allow" disabled={busy} onClick={() => void decideEdit(String(tool.result?.approval_id), true, false, true)}>允许发送</button>
                              </div></div></div>
                          )}
                          {tool.name === "convert_file" && tool.result?.status === "REVIEW" && typeof tool.result.approval_id === "string" && (
                            <div className="approval-card">
                              <div className="approval-heading"><span />等待审批</div>
                              <div className="approval-body"><p>允许本次转换文件并将原文件移入回收区？</p>
                              <div className="approval-detail"><div><span>源文件</span><pre>{String(tool.result.source_path ?? "")}</pre></div>
                              <div><span>目标文件</span><pre>{String(tool.result.target_path ?? "")}</pre></div>
                              <div><span>源内容</span><pre>{String(tool.result.original_content ?? "")}</pre></div>
                              <div><span>转换后内容</span><pre>{String(tool.result.new_content ?? "")}</pre></div></div>
                              <div className="approval-actions"><button disabled={busy} onClick={() => void decideEdit(String(tool.result?.approval_id), false, true)}>拒绝</button>
                              <button className="approval-allow" disabled={busy} onClick={() => void decideEdit(String(tool.result?.approval_id), true, true)}>允许一次</button></div></div>
                            </div>
                          )}
                          </div>
                        ))}
                      </div>
                    </article>
                  ))}
                  {active.id === liveReasoning?.sessionId && <div className="live-reasoning">{liveReasoning.steps.map((step, index) => step.kind === "reasoning" && step.text ? <details className="reasoning-row" key={index} open><summary>思考过程 <ChevronDown size={14} /></summary><div className="reasoning-content"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ img: ({ alt }) => <span>{alt || "图片"}</span> }}>{step.text}</ReactMarkdown></div></details> : null)}<TurnProcess items={liveReasoning.steps.filter((step): step is Extract<LiveStep, { kind: "tool" }> => step.kind === "tool")} running={busy} />{liveReasoning.answer && <div className="message-bubble assistant"><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={{ a: ({ children, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer">{children}</a>, img: ({ alt }) => <span>{alt || "图片"}</span>, table: ({ children }) => <div className="markdown-table-wrap"><table>{children}</table></div> }}>{liveReasoning.answer}</ReactMarkdown>{liveReasoning.answerInterrupted && <small className="answer-interrupted">回复未完成</small>}</div>}{busy && !liveReasoning.answer && <div className="thinking"><div className="message-avatar"><Sparkles size={14} /></div><span className="thinking-dots"><i /><i /><i /></span><span>正在执行</span></div>}</div>}
                </div>
              )}
            </div>

            {notice && <div className="error-banner"><Info size={16} /><span>{notice}</span><button onClick={() => setNotice("")} aria-label="关闭"><X size={15} /></button></div>}

            <div className="composer-wrap">
              <div className="composer">
                <textarea ref={textareaRef} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={handleKeyDown} placeholder="给 SecureAgent 发送消息…" rows={2} disabled={busy} />
                <div className="composer-tools"><div className="composer-context"><LockKeyhole size={14} /><span>授权工作区</span><span className="context-divider" /><span>{health?.model || "模型未配置"}</span></div><div className="composer-actions"><div className="context-usage" role="status" tabIndex={0} aria-label={usageLabel} title={usageLabel}>
                  <svg className="context-usage-ring" viewBox="0 0 20 20" aria-hidden="true"><circle className="context-usage-track" cx="10" cy="10" r="7" /><circle className="context-usage-fill" cx="10" cy="10" r="7" pathLength="100" strokeDasharray={`${usageBusy || usagePercent === null ? 0 : Math.min(100, usagePercent)} 100`} /></svg>
                  <span>{usageBusy ? "计算中" : usagePercent === null ? "--" : `${usagePercent.toFixed(1)}%`}</span>
                </div><button className="send-button" onClick={() => void sendMessage()} disabled={!draft.trim() || busy} aria-label="发送消息">{busy ? <span className="spinner" /> : <ArrowUp size={20} />}</button></div></div>
              </div>
              <div className="composer-disclaimer">SecureAgent 可能会出错。请核对重要信息和工具执行结果。</div>
            </div>
          </section>

          {inspectorOpen && <aside className="inspector">
            <div className="inspector-heading"><div><span className="inspector-eyebrow">SESSION PANEL</span><h2>运行详情</h2></div><button className="icon-button inspector-close" onClick={() => setInspectorOpen(false)} aria-label="关闭运行详情"><X size={16} /></button></div>
            <div className="status-card"><div className="status-card-top"><div className="status-icon"><Activity size={16} /></div><div><strong>{healthError ? "后端离线" : health?.configured ? "配置就绪" : "等待配置"}</strong><span>{healthError || (health?.configured ? "模型与工作区已配置" : "完成 .env 配置以开始任务")}</span></div></div><div className="status-divider" /><div className="status-meta"><span><i className={health?.configured ? "dot-green" : "dot-amber"} />配置状态</span><b>{healthError ? "离线" : health?.configured ? "已配置" : "未配置"}</b></div></div>
            <div className="inspector-section"><div className="section-heading"><span>安全边界</span><ShieldCheck size={15} /></div><div className="boundary-item"><div className="boundary-symbol"><Folder size={15} /></div><div><strong>授权工作区</strong><span className="path-value">{activeWorkspace?.path || (active?.serverId && !active.workspaceId ? health?.workspace : "") || "请选择工作区"}</span></div></div><div className="boundary-item"><div className="boundary-symbol"><LockKeyhole size={15} /></div><div><strong>工具调用策略</strong><span>每次调用均经可信端校验</span></div></div></div>
            <div className="inspector-section tools-section"><div className="section-heading"><span>已注册工具</span><span className="tool-count">{health?.tools?.length ?? "—"}</span></div>{toolList.map((tool) => { const Icon = tool.icon; const enabled = health?.tools?.includes(tool.name) ?? false; return <div className={`available-tool ${enabled ? "" : "tool-muted"}`} key={tool.name}><div className="available-tool-icon"><Icon size={15} /></div><div><strong>{tool.label}</strong><span>{tool.detail}</span></div><span className={`tool-state ${enabled ? "" : "inactive"}`}>{enabled ? "已启用" : "未启用"}</span></div>; })}</div>
            <div className="inspector-section"><div className="section-heading"><span>本地 Skill</span><button className="icon-button" onClick={() => setSkillRevision((value) => value + 1)} disabled={!activeWorkspace} aria-label="刷新本地 Skill" title="刷新本地 Skill"><RotateCcw size={14} /></button></div>
              {!activeWorkspace ? <p className="sandbox-note">请选择工作区。</p> : skillError ? <p className="skill-issue">{skillError}</p> : !skillCatalog ? <p className="sandbox-note">正在扫描…</p> : <>
                <p className="sandbox-note">技能正文由模型按需读取；不会自动执行脚本。</p>
                {!skillCatalog.skills.length && !skillCatalog.issues.length && <p className="sandbox-note">未发现本地 Skill。</p>}
                {skillCatalog.skills.length > 0 && <details className="skill-tree">
                  <summary><Folder size={14} /><span>已发现的 Skill</span><small>{skillCatalog.skills.length}</small></summary>
                  <div className="skill-tree-children">{Array.from(new Set(skillCatalog.skills.map((skill) => skill.source))).map((source) => {
                    const items = skillCatalog.skills.filter((skill) => skill.source === source);
                    return <details className="skill-tree-group" key={source}>
                      <summary><Folder size={14} /><span>{source}</span><small>{items.length}</small></summary>
                      <div className="skill-tree-children">{items.map((skill) => <details className="skill-tree-item" key={skill.path}>
                        <summary><FileText size={14} /><span>{skill.name}</span>{skill.conflict && <small className="skill-conflict">名称冲突</small>}</summary>
                        <div className="skill-entry"><span>{skill.description}</span><small>{skill.path}</small></div>
                      </details>)}</div>
                    </details>;
                  })}</div>
                </details>}
                {skillCatalog.issues.map((issue, index) => <p className="skill-issue" key={`${issue.path}-${index}`}>{issue.path}：{issue.error}</p>)}
                {skillCatalog.truncated && <p className="skill-issue">扫描数量达到上限，列表可能不完整。</p>}
              </>}
            </div>
            {health?.tools?.includes("run_tests") && <div className="inspector-section"><div className="section-heading"><span>测试沙箱</span></div><p className="sandbox-note">重建后，下次测试将使用最新工作区快照。</p><button className="sandbox-reset" disabled={busy || !active?.serverId} onClick={() => void resetSandbox()}>重建当前会话沙箱</button></div>}
            <div className="inspector-section activity-section"><div className="section-heading"><span>本轮活动</span><Clock3 size={14} /></div>{active?.messages.some((message) => message.tools?.length) ? <div className="activity-line"><span className="activity-marker" /><div><strong>工具调用已记录</strong><span>具体结果由会话回复说明</span></div></div> : <div className="activity-empty"><span>发送消息后，这里会显示本轮运行状态。</span></div>}</div>
            <div className="inspector-footer"><ShieldCheck size={14} /><span>本地运行 · 策略校验 · 最小权限</span></div>
          </aside>}
        </div>
      </main>
      {sidebarOpen && <button className="mobile-backdrop" onClick={() => setSidebarOpen(false)} aria-label="关闭导航" />}
    </div>
  );
}

export default App;
