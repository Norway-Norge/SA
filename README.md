# SecureAgent Harness

SA 是一个基于 LangGraph 的安全增强型 Agent，支持模型工具调用、执行端权限检查和 Docker 隔离测试。

## 环境与安装

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/) 和支持 Chat Completions、Tool Calling 的模型服务。Web 界面需要 Node.js；隔离测试需要 Docker。

```powershell
uv sync
Copy-Item .env.example .env
```

在 `.env` 中填写：

```dotenv
SA_API_KEY=你的模型密钥
SA_MODEL=你的模型名称
SA_BASE_URL=模型服务地址
```

## 运行

```powershell
# CLI
uv run python main.py --workspace G:\你的工作区

# Web 后端
uv run python main_web.py
```

在另一终端启动 Web 前端：

```powershell
cd frontend
npm ci
npm run dev
```

前端默认地址为 `http://127.0.0.1:5500/`，端口被占用时以终端输出为准。本地 API 默认不提供用户认证，请勿暴露给不受信任的网络。

## 测试

在包含 `tests/` 的开发仓库中运行（公开版本不含测试脚本）：

```powershell
uv run python -m unittest discover -s tests -v
```

## 项目架构

```mermaid
flowchart TD
    User[用户] --> UI[CLI / Web]
    UI --> Agent[LangGraph Agent 循环]
    Agent --> Model[模型 API]
    Agent --> Dispatcher[Dispatcher]
    Dispatcher --> Plugins[工具与安全插件]
    Plugins --> Services[Cordis 服务 / Provider]
    Agent --> Session[本地会话记录]
```

## 工具执行架构

```mermaid
flowchart TD
    Registry[工具注册表] --> Schema[Schema 快照]
    Schema --> Model[模型生成工具调用]
    Model --> Validate[Dispatcher 参数与注册校验]
    Validate --> Pre[前置策略与可选审批]
    Pre -->|允许| Guard[授权与 Guard 检查]
    Guard -->|允许| Execute[tools/execute 与工具执行]
    Execute --> Post[后置策略与输出处理]
    Validate -->|失败| Post
    Pre -->|拒绝或取消| Post
    Guard -->|拒绝| Post
    Post --> Result[工具消息与结果事件]
    Result --> Model
```

模型通过 Schema 提出调用；Dispatcher 执行校验、前置策略、审批及 Guard 检查，工具执行后再处理结果。Agent 可对大型结果进行 Spill，并广播 `tools/result`；直接调用 Dispatcher 时结果事件在后置处理后发送。部分工具还有独立审批。SA 已接入 stdio MCP 客户端；RAG 和跨系统通用幂等恢复尚未完整实现。
