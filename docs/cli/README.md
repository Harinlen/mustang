# CLI Docs

Mustang CLI 是 `src/cli/` 下的 TypeScript/Bun thin client。运行时边界是
WebSocket ACP：CLI 负责 terminal UI、输入、配置读取、kernel 连接、session
选择和 ACP event 渲染；kernel 负责 agent loop、model、tools、memory、prompt、
session truth 和本地执行。

## Start Here

| 文档 | 用途 |
|---|---|
| [design.md](design.md) | CLI 设计：ACP 边界、oh-my-pi active-port、目录和约束 |
| [history/](history/) | 已实现或历史计划，保留作为实现决策记录 |
| [../plans/cli-plan.md](../plans/cli-plan.md) | CLI 未完成工作和后续计划 |

## Current Facts

| 文档 | 状态 |
|---|---|
| [design.md](design.md) | 当前 CLI 设计事实 |
| [history/README.md](history/README.md) | 已实现阶段的历史记录 |

## Pending Work

| 文档 | 状态 |
|---|---|
| [../plans/cli-plan.md](../plans/cli-plan.md) | CLI future / not-yet-implemented work |
| [../plans/cli-active-port-prune-audit.md](../plans/cli-active-port-prune-audit.md) | draft audit；下一步适合拆小批删除 active-port 冗余文件 |
| [../plans/cli-interactive-keybindings-gap.md](../plans/cli-interactive-keybindings-gap.md) | investigation；记录 OMP keybinding parity 缺口 |

## Rules

- New or unfinished CLI work belongs under `docs/plans/`.
- `docs/cli/` is for implemented design facts and history.
- Keep kernel-side subsystem facts in `docs/kernel/`.
- If CLI needs new runtime capability, add/extend a kernel ACP method first;
  do not read kernel SQLite, sidecar files, or Python internals from CLI.
