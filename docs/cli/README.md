# CLI Docs

Mustang CLI 是 `src/cli/` 下的 TypeScript/Bun thin client。运行时边界是
WebSocket ACP：CLI 负责 terminal UI、输入、配置读取、kernel 连接、session
选择和 ACP event 渲染；kernel 负责 agent loop、model、tools、memory、prompt、
session truth 和本地执行。

## Start Here

| 文档 | 用途 |
|---|---|
| [design.md](design.md) | CLI 设计：ACP 边界、oh-my-pi active-port、目录和约束 |
| [roadmap.md](roadmap.md) | CLI 总计划、阶段状态、后续 reconnect 等未完成项 |
| [plans/](plans/) | 已实现或历史计划，保留作为实现决策记录 |
| [audits/](audits/) | 审计/调查类文档，不直接等同于实现计划 |

## Current Working Docs

| 文档 | 状态 |
|---|---|
| [audits/active-port-prune-audit.md](audits/active-port-prune-audit.md) | draft audit；下一步适合拆小批删除 active-port 冗余文件 |
| [audits/interactive-keybindings-gap.md](audits/interactive-keybindings-gap.md) | investigation；记录 OMP keybinding parity 缺口 |

## Historical / Implemented Plans

| 文档 | 状态 |
|---|---|
| [plans/phase-b-tui-migration.md](plans/phase-b-tui-migration.md) | historical Phase B plan |
| [plans/phase-b-ui-alignment-repair.md](plans/phase-b-ui-alignment-repair.md) | implemented |
| [plans/phase-c-permissions.md](plans/phase-c-permissions.md) | implemented |
| [plans/phase-d-session-config-theme.md](plans/phase-d-session-config-theme.md) | implemented |
| [plans/kernel-repl-bang-dollar.md](plans/kernel-repl-bang-dollar.md) | implemented |
| [plans/omp-first-refactor.md](plans/omp-first-refactor.md) | implemented |

## Rules

- Do not put new CLI work plans under `docs/plans/`; use `docs/cli/plans/`
  or `docs/cli/audits/`.
- Keep kernel-side protocol/subsystem plans in `docs/plans/` or
  `docs/kernel/` as appropriate.
- If CLI needs new runtime capability, add/extend a kernel ACP method first;
  do not read kernel SQLite, sidecar files, or Python internals from CLI.
