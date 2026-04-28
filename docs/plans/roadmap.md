# Roadmap

Unfinished work on the kernel rewrite.  See [`progress.md`](progress.md)
for what has already landed.

> **Note on phase numbering**: Phases 5.5–5.6 predate the kernel
> rewrite and targeted the archived `src/daemon/`.  They are kept at
> the bottom of this file for historical context only — no active work
> is tracked against them.  Live roadmap items for `src/kernel/` use
> the phase numbers assigned in `progress.md`.

---

## Standing gaps

- **ACP default** — `TransportFlags.stack` currently defaults to
  `dummy`; production builds select `acp` via `flags.yaml`.  Consider
  flipping the default once clients are known to speak ACP.

---

## ACP 跨 Session 通信

Claude Code 用 UDS (Unix Domain Socket) 做同机跨 session 消息传递。
Mustang 天然有 kernel/client ACP 协议层，跨 session 通信走 ACP 更自然：

| 对比 | Claude Code (UDS) | Mustang (ACP) |
|------|-------------------|---------------|
| 发现 | 文件系统扫描 socket 文件 | Kernel SessionManager 直接查 |
| 传输 | Unix socket 文件 | ACP WebSocket（已有） |
| 寻址 | `uds:<socket-path>` | `session:<session-id>` |
| 跨机器 | 不支持 | ACP/WS 天然支持 |
| 认证 | 无 | 复用 ConnectionAuthenticator |

| 项目 | 说明 |
|------|------|
| **Session discovery API** | Kernel 暴露活跃 session 列表（SessionManager 已有数据） |
| **Cross-session SendMessage** | `to="session:<id>"` 寻址另一个 session 的 agent |
| **ACP event relay** | Kernel 内部 session 间 event 转发，不需要额外 IPC |

前置依赖：SendMessage 基础功能完成。
Phase 1 在 SendMessageTool 的 `to` 解析中预留 `session:` prefix 分支位置。

---

## Team / Swarm — 多 Agent 协作

Claude Code 的 Team 模式：一个 team-lead 协调多个 teammate agent，各自独立运行，
通过 mailbox（文件系统 inbox）异步通信。

| 项目 | 说明 |
|------|------|
| **Team context** | team name, member list, role (lead/teammate) |
| **Mailbox 协议** | writeToMailbox / inbox polling，teammate 间异步消息 |
| **Broadcast** | `SendMessage(to="*")` 广播给所有 teammate |
| **Shutdown 协议** | team-lead 请求 teammate 关闭，teammate 可批准/拒绝 |
| **Plan approval** | teammate 提交 plan，team-lead 审批后才能执行 |
| **Permission 继承** | team-lead 的 permission mode 下发给 teammate |

前置依赖：SendMessage + Agent Resume + ACP 跨 Session 完成后再做。
Mustang 适配：mailbox 走 TaskRegistry 或 ACP event bus，不走文件系统。

---

## CLI 客户端（Active）

**Design**: [`docs/cli/design.md`](../cli/design.md)
**Docs**: [`docs/cli/README.md`](../cli/README.md)

Mustang 的第一个面向用户前端：thin ACP client，TUI 移植自
[oh-my-pi](https://github.com/can1357/oh-my-pi)（TypeScript/Bun）。
所有 agent 逻辑在 kernel 侧，CLI 只做 TUI 渲染和用户输入。

| Phase | 内容 | 优先级 |
|-------|------|--------|
| A | 骨架 + ACP 连接 + 最小 interactive loop | P0 |
| B | 完整 TUI 组件（tool execution, status line, markdown...） | P1 |
| C | 工具授权交互（approve/deny dialog） | P1 |
| D | session 管理 + 本地配置 + 主题 | P2 |

位置：`src/cli/`（Bun workspace，与 `src/kernel/` 并列）

---

## Future — web / IDE frontends

Browser UI connecting to the same kernel over ACP/WS.  Depends on CLI
client being stable first (proves the ACP client contract).
Features: markdown + image/PDF rendering, tool approval dialog, session
manager, task panel, plan-mode viz.

Native IDE integrations (VS Code, JetBrains) also become possible once
the ACP stack is stable — ACP is the same protocol those IDEs already
speak.

---

## MCP server management UI

Discover, install, configure, manage MCP servers.  Browse registry,
one-click install, guided config, health dashboard, enable/disable per
session.  Depends on credential store + web UI + MCPManager.

---

## Archived / deferred — pre-kernel backlog

The items below were planned against the old `src/daemon/`.  That
codebase has been archived under [`../../archive/daemon/`](../../archive/)
and the kernel rewrite has not picked these up.  Retained for reference
so ideas aren't lost if the kernel eventually needs similar features.

### Phase 5.5 — deferred

- **microCompact** — cache-aware partial tool-result cleanup.
- **Partial compact** — pivot-based selective compaction.

### Phase 5 — outstanding (low priority)

- **MCP concurrency optimization** — benchmark, semaphore tuning,
  per-server connection pooling, KEYED support for MCP tools.
- **Web fetch LLM post-processing** — sub-agent summarization of raw
  HTML output.
