# CLI Phase B UI 对齐修复计划

**父计划**: [`cli-plan.md`](cli-plan.md)  
**原 Phase B 计划**: [`cli-phase-b-tui-migration.md`](cli-phase-b-tui-migration.md)  
**范围**: `src/cli/` TypeScript / Bun client  
**状态**: planned  
**优先级**: P0，必须先于后续 CLI 功能开发

## 目标

修复 Phase B TUI 迁移，让 Mustang CLI 的主交互路径在视觉和交互行为上对齐
oh-my-pi `omp`。

这不是重新设计。目标是现有 oh-my-pi UI：搬运后的 runtime、搬运后的
editor/input 行为、搬运后的 status line 渲染、搬运后的组件契约，以及边界处的
Mustang ACP adapter。

## 问题陈述

当前 CLI 可以使用，但不满足最初 Phase B 的完成标准。

| 区域 | 当前状态 | 为什么这是 bug |
|---|---|---|
| Runtime 入口 | `main.ts` 启动 Mustang 自写的 `src/cli/src/modes/interactive.ts` | Phase B 要求使用 copied oh-my-pi `InteractiveMode`，或至少用 adapter 保留它的组件/input 语义 |
| Status line | `active-port/.../status-line.ts` 是 48 行 stub | upstream oh-my-pi status line 约 500 行，负责渲染 model/path/git/context/session 等 segment |
| ACP 映射 | `sessionUpdate` 处理直接写在 Mustang `InteractiveMode` 里 | Phase B 要求 ACP → oh-my-pi-like event 的转换放在 adapter 层 |
| 组件状态 | `InteractiveMode` 用临时 object literal 构造 copied components 的状态 | Phase B 要求通过 builder/adapter 保证组件状态匹配 copied component contract |
| 测试 | Phase B 报告主要依赖 smoke probe | 缺少和 oh-my-pi render output 的 golden/snapshot 对照，尤其是 status line/editor |

用户看到的症状就是 input/status 区域不像 `omp`，包括截图里的 status line。

## 非目标

- 本计划不加入 Phase D 的 session picker/config/theme 行为。
- 本计划不实现 kernel session delete/rename/archive。
- 本计划不搬运 oh-my-pi 的 provider/runtime/tool execution 逻辑。
- 本计划不设计一套新的 Mustang 视觉风格。
- 在 golden 对照和真实 PTY probe 通过前，不允许把 Phase B 标记为已修复。

## 参考表面

| 来源 | 用途 |
|---|---|
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/interactive-mode.ts` | upstream layout、containers、生命周期、input controller wiring |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/components/status-line.ts` | canonical status line 实现 |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/components/status-line/` | segment renderer、preset、separator、token-rate helper |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/components/custom-editor.ts` | app-level editor 快捷键和 border 集成 |
| `/home/saki/Documents/alex/oh-my-pi/packages/coding-agent/src/modes/controllers/input-controller.ts` | submit、escape、Ctrl+C、history、command 行为 |
| `src/cli/src/modes/interactive.ts` | 当前 Mustang bridge，目标是退役或缩薄 |
| `src/cli/src/active-port/coding-agent/session/agent-session.ts` | 当前过薄的 `AgentSession` facade |
| `src/cli/src/session.ts` | Mustang ACP session wrapper |
| `src/cli/src/acp/client.ts` | JSON-RPC pump 和 permission request handling |

## 设计原则

修复后的形状应当是：

```text
MustangSession / AcpClient
  -> MustangAgentSessionAdapter
  -> oh-my-pi-like AgentSession events/state
  -> copied InteractiveMode / InputController / components
  -> copied TUI runtime
```

`sessionUpdate` 必须在 adapter/session 边界处理，不能出现在 copied UI component
里，也不能散落在 `InteractiveMode` 中。

## R0 — 当前漂移清单

改代码前，先在实现 notes 或本文档里生成一个简短 drift inventory。

必须检查：

- 将 copied 文件与 upstream 做 diff：
  - `modes/interactive-mode.ts`
  - `modes/components/status-line.ts`
  - `modes/components/custom-editor.ts`
  - `modes/controllers/input-controller.ts`
  - `modes/components/welcome.ts`
  - `modes/components/tool-execution.ts`
- 对每个差异分类：
  - `boundary adaptation`：如果它只是用 ACP 替代 provider/agent-loop side effect，可以接受。
  - `dependency stub`：只有在截断 LSP/STT/OAuth/MCP/SSH 等排除能力时才可接受。
  - `visual simplification`：除非明确证明不可见，否则是 bug。
  - `behavior simplification`：除非明确记录为 out of scope，否则是 bug。
- 如果 copied 文件缺失或多余，更新 `active-port-manifest.json`。

验收：

- drift table 存在。
- `check_active_port.ts` 通过。
- visual/behavior simplification 被列为修复项，而不是隐藏掉。

## R1 — 恢复 Status Line 实现

用 upstream 实现加最小 Mustang 兼容 facade，替换 stub 掉的
`StatusLineComponent`。

必须保留的行为：

- 保留 upstream segment pipeline：presets、left/right segments、separators、
  token rate、path、git、model、context，以及数据可用时的 hooks/subagent/plan status。
- Mustang 暂时没有的数据必须降级为空/neutral segment value，不能重写组件。
- upstream 需要 git/path lookup 时，保留 CLI 本地 lookup。
- 增加一个薄的 `MustangStatusSession` / builder object，让组件收到
  oh-my-pi-like session shape。

可能涉及文件：

```text
src/cli/src/active-port/coding-agent/modes/components/status-line.ts
src/cli/src/active-port/coding-agent/modes/components/status-line/*
src/cli/src/session/state-builders.ts
src/cli/tests/test_status_line_golden.ts
```

验收：

- status line render tests 用 fixtures 对比 Mustang output 和 upstream oh-my-pi
  output，覆盖 no model、model、cwd path、git repo、plan mode、token counters。
- 截图级别的 status 区域使用与 `omp` 相同的 segment 顺序和 separator 规则。

## R2 — 建立 MustangAgentSessionAdapter

新增 adapter，把 ACP updates 和 Mustang session 操作转换成 copied oh-my-pi UI
期望的 state/events。

职责：

- 独占所有 `sessionUpdate` switch。
- 维护每轮 assistant text/thinking state。
- 维护 tool call lifecycle state。
- 维护给 status line 用的 session title/mode/model/context/token/status 数据。
- 暴露 `AgentSession`-like object，包含 `on` / `subscribe`、`prompt`、`abort`、
  shell/python execution wrappers，以及 copied controllers 需要的 state flags。
- 继续通过现有 `PermissionController` 处理 permission。

可能涉及文件：

```text
src/cli/src/session/agent-session-adapter.ts
src/cli/src/session/events.ts
src/cli/src/session/state-builders.ts
src/cli/src/session/history-storage.ts
src/cli/tests/test_agent_session_adapter.ts
```

验收：

- `rg "sessionUpdate" src/cli/src/active-port src/cli/src/tui` 没有结果。
- adapter tests 覆盖 streaming text、thinking chunks、tool start/progress/result、
  failed tools、mode updates、title updates、command updates，以及 user
  shell/python execution events。

## R3 — 缩薄或替换 Mustang InteractiveMode

移除当前自写 visual/layout implementation 作为主路径的地位。

优先路径：

- `main.ts` 启动 copied `active-port/coding-agent/modes/interactive-mode.ts`，
  并传入 `MustangAgentSessionAdapter`。
- Mustang 特有的 startup/connect/session bootstrap 留在 copied UI 之外。

如果 copied `InteractiveMode` 的 excluded surface 仍然过宽，则走 fallback：

- 保留 `src/cli/src/modes/interactive.ts`，但只能作为薄 subclass/facade。
- 它必须把 layout、editor、status line、input handling、component updates
  委托给 copied oh-my-pi classes/controllers。
- 它不能再用 ad-hoc object literal 构造 copied component state。

验收：

- `main.ts` 不再把当前 custom bridge 作为主 UI path 启动。
- copied `InputController` 和 `CustomEditor` 拥有 app-level input behavior。
- copied status/editor/welcome/tool components 通过 adapter events 或 state builders 更新。

## R4 — Editor 和 Input 行为对齐

对照 `omp` 验证 editor、快捷键、border/status 集成。

必须保留的行为：

- status line/editor border composition 匹配 upstream。
- Enter submit；Shift+Enter 插入换行。
- Escape 按 upstream 行为取消 stream/overlay。
- Ctrl+C interrupt 和 double-press exit 可用。
- Ctrl+L clear/redraw 行为匹配 upstream。
- Ctrl+R history search 要么激活，要么明确记录为 remaining bug。
- `!` / `!!` 和 `$` / `$$` 仍然走 kernel ACP execution。
- Slash autocomplete 通过 copied autocomplete/editor path 渲染。

验收：

- keyboard tests 使用 `TestTerminal` 或等价 fake terminal。
- PTY probe 证明真实 terminal 渲染和 teardown 正常。

## R5 — Golden 视觉回归套件

增加对照测试，防止这块再次漂移。

最小 golden 覆盖：

- Welcome first screen。
- 空 editor/status 区域。
- editor 中有短 prompt。
- editor 中有 multiline prompt。
- slash autocomplete 展开。
- assistant markdown/thinking。
- tool pending/running/completed/failed。
- status line with model/path/git/context fixtures。
- UI path 修改后 permission overlay 仍然可用。

对比策略：

- 优先用同一组 fixture 同时运行 upstream component 和 Mustang active-port component，
  比较 ANSI-aware rendered lines。
- 如果测试 runtime 无法直接 import upstream，则存储从 upstream commit 生成的明确
  golden snapshots，并记录 source commit / file timestamp。

验收：

- golden tests 纳入 `run_phase_b.ts`。
- 任何有意差异都进入小型 allowlist，并写明原因。
- 不能因为“Mustang 不一样”就接受视觉差异。

## R6 — 真实 Kernel / PTY Probe

Phase B repair 只有在通过真实 terminal 和真实 kernel connection probe 后才算完成。

probe 必须证明：

- CLI 能启动并连接 kernel。
- first viewport 的 status/editor 区域匹配 `omp` 结构。
- streaming text 能显示。
- tool call start/update/result 能显示。
- thinking chunk 能显示并正确折叠/渲染。
- slash autocomplete 能展开。
- prompt request pending 时 permission overlay 仍能出现。
- Ctrl+C 能取消 active work。
- `!` 和 `$` execution 仍然走 kernel ACP。

推荐命令形状：

```bash
bun run src/cli/tests/run_phase_b.ts
bun run src/cli/tests/probe_phase_b_pty.ts
```

验收：

- completion report 粘贴 probe output。
- screenshot 或 captured ANSI frame 只有在必要时才保存为 test artifact；
  除非是刻意保持很小的 golden fixture，否则不要提交笨重的 terminal recording。

## Closure-Seam Inventory

实现时预计会触碰的 closure seams：

| Seam | Caller | Callee | Required probe |
|---|---|---|---|
| ACP update adapter | `AcpClient` / `MustangSession` | `MustangAgentSessionAdapter` | fake ACP adapter tests + real kernel streaming probe |
| Permission request handler | `AcpClient` | `PermissionController` / copied overlay | existing Phase C tests + real pending-prompt permission probe |
| Prompt submit | copied `InputController` | `MustangAgentSessionAdapter.prompt()` | keyboard test + real kernel prompt probe |
| Cancel | copied editor/input controller | `MustangSession.cancel()` / `cancelExecution()` | PTY cancel probe |
| Status state builder | adapter | copied `StatusLineComponent` | golden status tests |
| Shell/Python execution | copied input path | kernel `session/execute_*` ACP methods | real shell/python ACP probe |

## 验证矩阵

报告完成前必须运行：

```bash
bunx tsc -p src/cli/tsconfig.json --noEmit
bun run src/cli/scripts/check_active_port.ts
bun run src/cli/tests/run_all.ts
bun run src/cli/tests/run_phase_b.ts
bun run src/cli/tests/run_phase_c.ts
bun run src/cli/tests/probe_phase_b_pty.ts
```

如果触碰 shell/python 或 permission 路径，还要跑 kernel-side targeted checks：

```bash
uv run pytest tests/e2e/test_ask_user_question_e2e.py -q -m e2e
uv run pytest tests/kernel/session/test_permission_options.py -q
```

## 完成标准

只有满足以下条件，repair 才算完成：

- production CLI entry 使用修复后的 oh-my-pi-compatible path。
- `StatusLineComponent` 不再是 visual stub。
- `sessionUpdate` 被隔离在 adapter/session code。
- golden tests 证明 component render parity against `omp`。
- 真实 PTY probe 证明 first-screen/status/editor behavior 在真实 terminal 中正确。
- progress docs 明确更正此前 "first usable Phase B" 状态为 partial，而不是 complete。

