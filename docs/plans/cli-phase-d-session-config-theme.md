# CLI Phase D — Session 管理、本地配置与主题计划

**父计划**: [`cli-plan.md`](cli-plan.md)
**范围**: `src/cli/` TypeScript / Bun client
**状态**: planned
**前置**: Phase A/B/C 已完成；Phase E 断线重连不在本阶段实现

## 目标

Phase D 把 CLI 从“能进入一个新会话聊天”补齐为可长期使用的本地客户端：

- 启动时可以选择、恢复最近 session，而不是每次默认新建。
- CLI 有自己的本地配置文件，统一管理 kernel 连接、token 来源、默认 session 行为、
  UI 偏好和主题。
- 主题系统从 active-port 状态变成用户可配置能力。
- 可选实现本地 kernel 自启动，但只覆盖启动前连接失败的场景；运行中断线重连留给
  Phase E。

Phase D 仍保持 CLI thin-client 边界：所有会话数据真相来自 kernel ACP
`session/list` / `session/load`，CLI 不保存对话 transcript，也不直接读取 kernel
SQLite。

## 设计取舍

### 配置文件路径

CLI 设计文档使用 `~/.mustang/client.yaml`，父计划早期草案写的是
`~/.mustang/config.yaml`。Phase D 采用前者作为 canonical path：

```text
~/.mustang/client.yaml
```

原因：

- `config.yaml` 已经是 kernel 业务配置的自然名称；CLI 写入它容易和 kernel
  layered config 混淆。
- `client.yaml` 明确表示这是前端偏好，不是 kernel 子系统配置。
- 迁移成本低：Phase D 可以只读 `client.yaml`；如发现已有旧
  `~/.mustang/config.yaml` 的 CLI 字段，再提供一次性兼容读取并提示迁移。

### Session 选择入口

默认启动策略：

1. `--session <id>` 明确给出时，直接 `session/load`。
2. `--new` 明确给出时，直接 `session/new`。
3. 有 positional prompt 或 `--print` 时，默认新建 session，避免 TUI 选择器阻塞脚本。
4. 交互模式且配置 `session.startup = "picker"` 时，先拉 `session/list` 并展示选择器。
5. 交互模式且配置 `session.startup = "last"` 时，恢复最近 session；没有历史则新建。
6. 交互模式且配置 `session.startup = "new"` 时，直接新建。

初始默认建议为 `picker`，但如果 `session/list` 为空则自动新建。

### 主题加载

当前 `src/cli/src/active-port/coding-agent/modes/theme/` 已带入 oh-my-pi 主题系统和
大量内置 JSON 主题。Phase D 不重写主题引擎，只新增 Mustang 配置读取和启动注入：

- `ui.theme`: 主题名，默认 `dark`。
- `ui.auto_theme`: 跟随系统深浅色，可选。
- `ui.symbols`: `unicode` / `nerd` / `ascii`。
- `ui.status_line`: 是否显示 status line。

`InteractiveMode` 不再硬编码 `initTheme(false)`，而是接收配置后的 theme options。

### Kernel 自启动

自启动是 Phase D 的可选子项，只处理“启动时连接失败”：

- 配置 `kernel.autostart = true` 才启用。
- 只对 loopback URL 生效，远程 URL 不自动 spawn。
- spawn 命令来自配置，默认使用当前 repo 下的 kernel 启动命令。
- readiness 用 HTTP health endpoint 或 ACP connect retry 验证。
- CLI 退出时只清理自己启动的 kernel 进程；连接到既有 kernel 时绝不 kill。

运行中断线、指数退避和 session 恢复属于 Phase E。

## 参考表面

| 来源 | 用途 |
|---|---|
| `src/kernel/kernel/protocol/acp/routing.py` | `session/list` / `session/load` wire shape |
| `src/kernel/kernel/protocol/acp/schemas/session.py` | ACP session summary schema |
| `src/probe/probe/client.py` | Python ACP client 对 session 请求的参考 |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/components/session-selector.ts` | TUI session picker 主体 |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/cli/session-picker.ts` | standalone picker lifecycle |
| `docs/plans/session-lifecycle-actions.md` | kernel-side plan for user-visible delete / rename / archive |
| `src/cli/src/active-port/coding-agent/modes/theme/theme.ts` | 已 port 的主题加载、内置主题、custom theme 目录 |
| `src/cli/src/main.ts` | 当前 argv / connect / session bootstrap |
| `src/cli/src/modes/interactive.ts` | 当前 TUI 初始化和 `initTheme(false)` 调用点 |
| `src/cli/src/session.ts` | 当前 `session/new` / `session/load` wrapper |

## 当前实现快照

| 项 | 当前状态 | Phase D 要补的缺口 |
|---|---|---|
| Session 创建 / 恢复 | `--session` 可 load；无 flag 总是 create | 缺 `session/list` wrapper、picker、启动策略、最近 session resume |
| Welcome recent sessions | `WelcomeComponent` 支持 recent sessions 入参 | 当前传空数组，未接 kernel session summaries |
| Slash `/session` | autocomplete 中存在 `/session` | 本地未处理；Phase D 至少支持 picker / new / current |
| 本地配置 | 只读 env、`--port`、token 文件 | 缺 typed config loader、schema、defaults、env/argv precedence |
| Token | `MUSTANG_TOKEN` 或 `~/.mustang/state/auth_token` | 缺配置指定 token 文件 / literal token / password auth 预留 |
| Theme | active-port 主题文件已存在 | 缺用户选择、config 注入、主题列表测试 |
| Kernel 自启动 | 未实现 | 可选 D6；失败时保留当前清晰错误路径 |

### 2026-04-27 风险核验快照

对照当前 `src/kernel/`、`src/cli/` 和 docs 后的事实：

| 检查项 | 结论 | 证据 |
|---|---|---|
| `session/list` / `session/load` 可作为 Phase D 主 seam | 已实现 | `src/kernel/kernel/protocol/acp/routing.py` dispatch 中有 `session/list` / `session/load`；`src/kernel/kernel/protocol/acp/schemas/session.py` 定义 `AcpSessionInfo(session_id, cwd, created_at, title)` |
| session delete | 仅内部实现，未暴露给 CLI/ACP | `SessionStore.delete_session()` 和 `SessionManager.delete_session()` 存在，但 `routing.py` 无 `session/delete`；`docs/kernel/interfaces/protocol.md` 标注 `session/delete` 待实现；`docs/kernel/subsystems/commands.md` 标注 ACP 方法缺失 |
| session rename | 仅自动标题更新路径，未暴露用户 rename API | `SessionStore.update_title()` 存在，`session_info_changed` 可更新 title；未发现 `session/rename` routing 或 CLI handler。active-port 里有 oh-my-pi `app.session.rename` keybinding，但当前 Mustang facade `active-port/.../session/session-manager.ts` 为空实现 |
| session archive | 未发现 session 级 archive 能力 | `archive` 搜索只命中 repo/archive、memory log archive、theme/file type；无 `session/archive` routing/store/API |
| upstream session selector 直接可用性 | 需要 adapter | upstream selector 选择 `SessionInfo.path` 并可调用 `FileSessionStorage.deleteSessionWithArtifacts()`；Mustang 必须把 `sessionId` 投影到组件字段，且不能启用文件删除路径 |
| CLI active-port session manager | 当前是 stub | `src/cli/src/active-port/coding-agent/session/session-manager.ts` 的 `getRecentSessions()` 返回 `[]` |
| 主题系统 | 已复制但未配置化 | `theme.ts` 有内置/custom theme 加载；当前 `InteractiveMode.run()` 仍硬编码 `initTheme(false)` |
| 配置文件命名风险 | 真实存在 | CLI 设计文档写 `~/.mustang/client.yaml`，旧父计划写 `~/.mustang/config.yaml`；Phase D 采用 `client.yaml` 作为 canonical path |

## D0 — 配置和启动状态模型

新增 CLI 本地配置模块：

```text
src/cli/src/config/
├── schema.ts
├── loader.ts
└── paths.ts
```

建议 schema：

```yaml
kernel:
  url: ws://localhost:8200
  token: null
  token_file: ~/.mustang/state/auth_token
  autostart: false
  autostart_command: null
  health_url: http://localhost:8200/

session:
  startup: picker        # picker | last | new
  list_scope: cwd        # cwd | all
  picker_limit: 50
  restore_cwd: true

ui:
  theme: dark
  auto_theme: false
  symbols: unicode       # unicode | nerd | ascii
  status_line: true
  welcome_recent: 3
```

Precedence：

1. hardcoded defaults
2. `~/.mustang/client.yaml`
3. environment variables：`KERNEL_URL`、`KERNEL_PORT`、`MUSTANG_TOKEN`
4. argv：`--kernel`、`--port`、`--session`、`--new`、`--theme`

验收：

- loader 对缺失文件返回 defaults。
- malformed YAML fail closed，显示文件路径和字段错误。
- env/argv 覆盖顺序有单测。
- token 读取支持 env token、literal config token、token file，且报错不打印 token 内容。

## D1 — ACP Session 列表 wrapper

扩展 `MustangSession` 或新增 `src/cli/src/sessions/` 边界模块，提供：

```text
src/cli/src/sessions/
├── types.ts
├── mapper.ts
└── service.ts
```

职责：

- 调 `session/list`，支持 `cwd` filter 和 cursor pagination。
- 将 kernel `AcpSessionInfo` 映射成 UI-facing `CliSessionInfo`。
- 保留原始 `sessionId`，不要使用 oh-my-pi 的 file path 作为会话标识。
- 兼容 session summary 字段缺失：title / cwd / timestamps / token counters 缺失时仍可渲染。

关键约束：

- CLI 不读 kernel SQLite。
- `session/load` 仍只传 `sessionId`、`cwd`、`mcpServers`。
- list 失败时不阻塞新建 session；显示 warning 后 fallback create。

验收：

- fake ACP tests 覆盖空列表、分页、多 cwd、字段缺失。
- real kernel probe 覆盖 `session/list -> select id -> session/load`。

## D2 — Session 选择器 active-port

把 oh-my-pi `session-selector.ts` 作为 UI 主体迁入 active-port：

```text
src/cli/src/active-port/coding-agent/modes/components/session-selector.ts
```

如果 upstream 组件强依赖 `SessionInfo.path` / `FileSessionStorage`：

- 保留 render / input 主体。
- 在 Mustang mapper 中把 `sessionId` 投影到组件需要的 `path` 字段，选择后再映射回
  `sessionId`。
- 删除 / rename / archive 不在 D0-D5 范围内启用。当前 kernel 只有内部
  `delete_session()`，没有用户 ACP route；rename 只有自动标题更新路径；archive 未实现。
  因此 selector 里的 Delete/Rename 入口必须隐藏、禁用或提示 “not available yet”，
  不能调用 upstream `FileSessionStorage`。Kernel 侧补齐方案见
  [`session-lifecycle-actions.md`](session-lifecycle-actions.md)。

启动 picker lifecycle 可参考 upstream `cli/session-picker.ts`，但 Mustang 版本必须使用
现有 `@/tui/index.js` facade 和 `ProcessTerminal`，不能引入新的 oh-my-pi package
依赖。

交互要求：

- 搜索框支持 fuzzy filter。
- 上下键、PageUp/PageDown、Enter、Esc、Ctrl+C 行为与 upstream 一致。
- Esc 取消后按配置 fallback：默认新建 session。
- Ctrl+C 在 picker 内退出 CLI，exit code 0。
- 空列表直接返回 “new session”。

验收：

- render test：空列表、短列表、长列表滚动、title fallback、cwd 显示。
- keyboard test：搜索、选择、Esc fallback、Ctrl+C exit path。
- active-port manifest 包含新增 copied 文件。

## D3 — 启动编排重构

把 `main.ts` 中的启动流程拆成可测试单元：

```text
src/cli/src/startup/
├── args.ts
├── connect.ts
├── session-startup.ts
└── autostart.ts
```

目标流程：

```text
parse argv
  -> load config
  -> resolve kernel endpoint + token
  -> connect or optional autostart
  -> resolve session startup mode
  -> init theme
  -> run InteractiveMode
```

注意：

- `main.ts` 只保留 thin orchestration 和 `process.exit`。
- `--session` / `--new` 互斥，冲突时打印 usage 并 exit 2。
- `--print` / positional prompt 如已存在，不能被 picker 阻塞。
- session startup 失败时，只有明确 `--session` 的 load failure 是 fatal；picker/last
  的 load failure 可以 fallback new 并提示。

验收：

- 单测覆盖 argv/config/env precedence 和 startup branch。
- 当前 Phase A `run_all.ts` 仍通过。

## D4 — Welcome 和 `/session` 本地命令

把 session 信息带进当前 TUI：

- `WelcomeComponent` 的 recent sessions 入参使用 `session/list` 最近 N 条。
- status line session title 在 load 后立即使用 summary title；后续仍由
  `session_info_update` 更新。
- 本地 `/session` 命令最小支持：
  - `/session` 打开 picker。
  - `/session new` 新建 session 并切换当前 TUI。
  - `/session current` 显示当前 session id / title / cwd。

切换 session 时：

- 如果当前 prompt 正在运行，拒绝切换并提示先 cancel。
- 清空当前 chat view 或插入明确分隔提示；不要把两个 session 的流混在同一个消息树里。
- 重新安装 update handler 使用新的 `MustangSession`。

验收：

- fake session service tests 覆盖 picker selection 后切换当前 session。
- TUI test 覆盖 busy 时 `/session` 被拒绝。

## D5 — 主题配置接入

把 config 的 UI 字段传入 theme 初始化和 interactive mode：

- `main.ts` / startup 读取 `ui.theme`、`ui.auto_theme`、`ui.symbols`。
- `InteractiveMode.run()` 调用配置化的 `initTheme(...)`，不再硬编码 `false`。
- 如果主题名不存在，fallback `dark` 并显示 warning。
- `--theme <name>` 覆盖配置。
- 可选本地命令 `/theme`：
  - `/theme` 显示当前主题。
  - `/theme list` 列出内置 + custom themes。
  - `/theme set <name>` 本次进程切换，并可选写回 `client.yaml`（写回可延期）。

验收：

- theme loader 单测覆盖内置主题、缺失主题 fallback、custom theme dir。
- snapshot/render test 至少覆盖 dark 和 light 两个主题下的 welcome/status/editor。
- `bunx tsc` 不因为 theme JSON imports / typebox schema 新增错误。

## D6 — 可选 Kernel 自启动

实现为独立、可跳过批次；如果时间不够，Phase D 可以先标记 deferred，不影响 D0-D5
交付。

文件：

```text
src/cli/src/startup/autostart.ts
```

行为：

- 仅当 `kernel.autostart = true` 且初次 connect 失败时尝试。
- 只允许 loopback host：`localhost` / `127.0.0.1` / `::1`。
- `autostart_command` 缺失时使用安全默认；如果当前 repo 路径无法判断，直接提示配置命令。
- readiness 最多等待 15 秒。
- 记录 spawned process handle，CLI 正常退出时 terminate；异常退出时尽量 cleanup。

验收：

- 单测用 fake spawn/readiness，不启动真实 kernel。
- 手动 probe：无 kernel 运行时，CLI autostart 后能进入 TUI 并创建 session。

## D7 — Phase D 测试矩阵

新增或扩展：

```text
src/cli/tests/
├── run_phase_d.ts
├── test_config_loader.ts
├── test_session_list_mapper.ts
├── test_session_picker.ts
├── test_session_startup.ts
├── test_theme_config.ts
└── test_autostart.ts          # 若 D6 实现
```

真实 kernel closure-seam probes：

- `session/list` real ACP request 返回可渲染 summaries。
- picker 选择一个真实 summary 后，`session/load` 成功恢复。
- `client.yaml` 指定 kernel URL/token file 后，CLI 能连接真实 kernel。
- theme config 注入后，`InteractiveMode` 首屏渲染使用指定 theme。

命令：

```bash
/home/saki/.bun/bin/bunx tsc -p src/cli/tsconfig.json --noEmit
BUN_BIN=/home/saki/.bun/bin/bun /home/saki/.bun/bin/bun run src/cli/scripts/check_active_port.ts
BUN_BIN=/home/saki/.bun/bin/bun /home/saki/.bun/bin/bun run src/cli/tests/run_phase_d.ts
BUN_BIN=/home/saki/.bun/bin/bun KERNEL_URL=ws://127.0.0.1:8200 /home/saki/.bun/bin/bun run src/cli/tests/run_all.ts
```

如果 D6 自启动实现，还要跑一条无 kernel 预启动的 manual probe，并在完成报告贴输出。

## 完成标准

Phase D 完成时必须满足：

- 无 `--session` 的交互启动按配置进入 picker / last / new。
- picker 能从真实 kernel `session/list` 获取 session，并通过 `session/load` 恢复。
- `~/.mustang/client.yaml` 被 typed loader 读取，env/argv 覆盖顺序明确且有测试。
- token 来源支持 env、config literal、config token file、默认 token file。
- 主题可通过 config 和 `--theme` 选择，缺失主题有 fallback。
- Welcome recent sessions 不再永远为空。
- `/session` 至少支持 picker、新建和当前 session 信息。
- active-port 新增文件都登记在 manifest，检查脚本通过。
- TypeScript typecheck、Phase D 本地测试、Phase A smoke 测试通过。
- 若实现 D6，自启动 probe 输出必须贴在完成报告；若不实现，父计划和本文档状态明确标为
  deferred，不影响 D0-D5 完成。

## 非目标

- 不实现运行中断线检测和自动重连；那是 Phase E。
- 不实现用户可见的 session 删除、重命名、归档。已验证：delete 只有 kernel
  内部 reaper/store 能力，没有 `session/delete` ACP route；rename 只有自动标题更新路径，
  没有 `session/rename`；archive 未发现 session 级能力。若用户要求这些功能，需要先补
  kernel ACP 方法和 CLI 命令，再接 selector 按键。Kernel 计划见
  [`session-lifecycle-actions.md`](session-lifecycle-actions.md)。
- 不把 session transcript 存到 CLI 本地。
- 不让 CLI 直接读取 kernel SQLite。
- 不迁移 oh-my-pi agent loop、provider、tool runtime。
- 不新增 Web / IDE 前端配置。

## 风险与关闭方式

| 风险 | 当前验证 | 关闭方式 |
|---|---|---|
| `client.yaml` 与旧 `config.yaml` 命名冲突 | 已验证：CLI design doc 与旧父计划确有分歧 | 文档明确 canonical path；如实现兼容读取，只读不写旧路径并给 migration 提示 |
| upstream session selector 用 file path，不适配 ACP session id | 已验证：upstream selector 回传 `session.path` | mapper 层投影 `sessionId -> path`，UI 选择后映射回 id；不让 path 进入 ACP |
| upstream selector 暗含删除能力 | 已验证：upstream `session-picker.ts` 调 `FileSessionStorage.deleteSessionWithArtifacts()`；kernel 无用户 `session/delete` ACP | D0-D5 隐藏/禁用删除入口；若后续要做删除，先实现 `session/delete` ACP + CLI command |
| rename/archive 以为已实现 | 已验证：active-port 有 keybinding/type，Mustang kernel 无用户 route；archive 未实现 | D0-D5 不暴露 rename/archive；后续单独设计 kernel ACP seam |
| picker 阻塞脚本 / print 模式 | 当前 main 还没有 print path，但 Phase D 会扩展 argv | 启动策略明确：非交互或 prompt 参数默认不打开 picker |
| 主题初始化过早，组件拿到旧 singleton | 已验证：当前 `InteractiveMode.run()` 硬编码 `initTheme(false)` | config 解析在 `InteractiveMode.run()` 前完成，所有组件创建前调用 configured `initTheme` |
| 自启动误杀用户已有 kernel | 当前未实现 | 只保存并 cleanup 本进程 spawn 的 child process；既有连接不归 CLI 管 |
| active-port 膨胀 | 已验证：当前 active-port 已含大量 theme 文件，selector 尚未复制 | 只复制 session selector 闭包；manifest 检查新增文件；发现外设依赖用 facade 截断 |
