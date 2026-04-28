# Progress — Kernel Era

Single source of truth for completed work on `src/kernel/` (the rewrite).
Update after every implementation task.  Route gotchas to
[`../lessons-learned.md`](../lessons-learned.md).

> **Pre-kernel history**: daemon-era progress docs have been removed.
> All relevant lessons are captured in [`../lessons-learned.md`](../lessons-learned.md).

## Current Status

**Active**: Phase 17 — SecretManager + BashClassifier gap closure + GitManager landed.
**Kernel version**: 2.0.0 (schema v2, session archive/title-source metadata).
**Test suite**: 1710 passing (unit) + e2e.

**Kernel rewrite status**:

- **Implemented** (real code, used in prod path): Flag, Config,
  SecretManager (bootstrap service), ConnectionAuthenticator,
  ToolAuthorizer, LLMProviderManager, LLMManager, ToolManager,
  SkillManager, HookManager, SessionManager, StandardOrchestrator,
  CommandManager (9 builtin commands), GatewayManager (with
  DiscordAdapter), MCPManager, MemoryManager, PromptManager (bootstrap
  service, 21 prompt files), GitManager.
- **Protocol/Transport**: both stacks live in `kernel.routes.stack`.
  `dummy` stack (identity pass-through, default) and `acp` stack
  (`kernel.protocol.build_protocol_stack`) are both registered;
  production selects `acp` via `flags.yaml`.
- **No skeleton-only subsystems remain**. All registered subsystems
  have real `startup` / `shutdown` implementations.

## Completion Table

| Step | Feature | Notes |
|------|---------|-------|
| Kernel bootstrap | Config, Flags, Auth, app.py lifespan | |
| LLM subsystem | LLMProviderManager + AnthropicProvider + OpenAICompatibleProvider + BedrockProvider stub | |
| LLM manager | LLMManager — model config, alias resolution, ModelHandler Protocol | |
| Protocol layer | ACP codec, multi-target routing, SessionHandler, ModelHandler, handshake | |
| Transport layer | WS `/session` — auth, stack selection, recv/encode/send loop | |
| Session layer | SessionManager, JSONL persistence, Orchestrator lifecycle, multi-connection broadcast | |
| Orchestrator | StandardOrchestrator — LLM loop, history, compaction, tool executor | |
| Session storage → SQLite | Replace JSONL + index.json with `sessions.db`; token tracking; auto-migration; 39 tests | plan: `kernel/subsystems/session.md (appendix)` |
| Versioning convention | major=schema, minor=subsystem, patch=bugfix; SCHEMA_VERSION=major; KERNEL_VERSION from `__version__`; bump to 1.0.0 | decisions D20, D21 |
| CommandManager | Slash command catalog subsystem (#10). `CommandDef` + `CommandRegistry` + 7 built-in commands. No dispatch — WS clients self-dispatch via ACP; gateway adapters call `_execute_for_channel`. | design: `kernel/subsystems/commands.md` |
| GatewayManager | External messaging platform subsystem (#11). `GatewayAdapter` ABC + `DiscordAdapter` (Gateway WS + REST). Permission round-trip over chat, per-session lock, async fire-and-forget dispatch. Session fix: `_get_or_load` for evicted sessions. | design: `kernel/subsystems/gateways.md` |
| probe 0.1.0 | `src/probe/` — interactive + machine-readable ACP test client | `python -m probe` (interactive) · `--test --prompt` (JSON output) |
| ToolManager + ToolAuthorizer M1/M2 | Tool ABC, ToolContext, FileStateCache, ToolRegistry with `snapshot_for_session`, 6 built-in tools (Bash/FileRead/FileEdit/FileWrite/Glob/Grep), PermissionRule DSL + RuleStore + RuleEngine + SessionGrantCache + BashClassifier (LLMJudge stub) + `filter_denied_tools()`, orchestrator `ToolExecutor` 7-step flow, `OrchestratorDeps.authorizer` + `should_avoid_prompts_provider`, SessionManager wiring. 63 new tests (388 passing). | design: `kernel/subsystems/tools.md` + `kernel/subsystems/tool_authorizer.md` |
| LLM `current_used` refactor | `LLMConfig.default_model: str` → `LLMConfig.current_used: CurrentUsedConfig` (role → model ref, `default` the only seeded role). `LLMManager.default_model` property → `model_for(role)` method with startup-time fail-fast validation. ACP wire format (`model/set_default` / `default_model` response field) preserved for client stability. 389 tests passing. | design: `kernel/subsystems/llm.md (appendix)` |
| HookManager skeleton | Subsystem scaffold: 14-event enum (POST_SAMPLING added post-M3), `HookEventCtx`, `HookBlock` exception, EVENT_SPECS matrix, user/project manifest discovery + parsing, boundary safety, `fire(ctx)` API. Fire-sites still TODO at this point; probe e2e workflow landed. | design: `kernel/subsystems/hooks.md` |
| ToolAuthorizer M3 | HookManager fire-sites wired (`pre_tool_use` / `post_tool_use` / `post_tool_failure` in ToolExecutor; `permission_denied` / `permission_requested` in ToolAuthorizer). `OrchestratorDeps.queue_reminders` + `drain_reminders` closures drain `ctx.messages` into `Session.pending_reminders`; Orchestrator prepends `<system-reminder>` blocks on the next turn. BashClassifier real LLMJudge landed: resolves via `LLMManager.model_for("bash_judge")`, streams with XML-bounded prompt, parses JSON verdict, honors `fail_closed` on stream error, denial-tracking budget (MAX_CONSECUTIVE=3 / MAX_TOTAL=20) trips at the existing call-site. +26 tests (468 passing). | design: `kernel/subsystems/hooks.md` + `kernel/subsystems/tool_authorizer.md` |
| MCPManager | Full MCP subsystem: 4 transports (stdio/SSE/HTTP/WebSocket), McpClient (JSON-RPC + handshake + exponential-backoff reconnect), config loading (ConfigManager 3-layer + `.mcp.json` compat), policy filtering (allow/deny), health monitor background task, MCPAdapter (Tool ABC wrapper for MCP tools) + ToolManager `_sync_mcp()` signal integration. +75 tests (596 kernel + 6 e2e passing). | design: `kernel/subsystems/mcp.md` |
| PromptManager | Centralise all prompt text in `.txt` files under `kernel/prompts/default/`. PromptManager is a bootstrap service (not Subsystem). 7 prompts migrated from hardcoded Python. User override layers: `~/.mustang/prompts/` (global) and `<project>/.mustang/prompts/` (project-local, highest priority); `app.py` auto-discovers at startup; missing dirs silently skipped. ToolSearchTool description migrated to `prompts/default/tools/tool_search.txt` (was hardcoded `_DESCRIPTION`); added missing CC "Result format:" paragraph. +6 unit tests + 2 e2e tests. | design: `kernel/subsystems/prompts.md` |
| SkillManager | Full skill subsystem: SKILL.md frontmatter parsing (Claude Code + Hermes fields), multi-layer recursive discovery (.mustang/ + .claude/ compat), three-pool registry (static/conditional/dynamic), lazy body loading, $ARGUMENTS/${name}/${SKILL_DIR}/${config.*} substitution, Hermes setup flow, SkillTool (Tool ABC), PromptBuilder listing injection, ToolExecutor on_file_touched dynamic discovery, compaction preservation, bundled skills framework, disk snapshot cache, CommandManager /skill autocomplete. +105 unit tests + 7 e2e test files (716 kernel tests total). | design: `kernel/subsystems/skills.md` |
| Streaming & parallel tool execution | ToolExecutor upgraded to streaming-shaped interface (`add_tool`/`finalize_stream`/`results`/`discard`). `partition_tool_calls` splits tool_calls into batches (consecutive safe tools → parallel batch, unsafe → singleton). Concurrent batch execution via `asyncio.create_task` + `asyncio.Queue` per-tool event merge + `asyncio.Semaphore(max_concurrency)` throttle. `asyncio.Lock` serializes `on_permission` prompts across concurrent tools. Orchestrator creates per-turn executor; supports `streaming_tools=True` (stream-inline `add_tool`) and `False` (post-stream batch). `OrchestratorConfig.streaming_tools` flag added. Legacy `run()` preserved as wrapper. +9 parallel tests (870 kernel tests total). Prerequisite for AgentTool. | design: `kernel/subsystems/tools.md` § 6.3, § 10 |
| Orchestrator STEP 3: POST_SAMPLING hook + abort check | `HookEvent.POST_SAMPLING` added (14-event enum), fires after every LLM stream ends, before abort check and tool/stop branching (`can_block=False`, notification-only). Abort check ①: `await asyncio.sleep(0)` checkpoint after post_sampling, before `append_assistant()`. Cancel handler enhanced: `pending_tool_use_ids()` detects orphan tool_use blocks, synthesises `ToolResultContent(is_error=True)` to keep history well-formed for Anthropic API. +11 unit tests + 3 e2e tests (895 kernel tests total). | CC ref: `query.ts:999-1052` |
| FileRead media support | FileReadTool extended: image files (PNG/JPEG/WebP/GIF) returned as `ImageContent` for multimodal LLM; PDF documents rendered to PNG pages via PyMuPDF (`pymupdf` optional dep, 150 DPI). `pages` param for page range selection, 20-page/request limit, >10-page auto-limit. Page parser with range/comma/dedup support. `_coerce_content` passes mixed `TextContent`+`ImageContent` blocks through as list. +23 new unit tests (53 total for file_read) + 3 e2e tests. | roadmap: Phase 5 backlog item |
| TaskManager + AgentTool | TaskRegistry (per-session, not a Subsystem): in-memory task state, file-based output collection, notification drain, observer pattern. 3 new builtin tools: TodoWriteTool, TaskOutputTool, TaskStopTool. AgentTool: sub-agent spawning via `spawn_subagent` closure on ToolContext. Orchestrator step 6d: drain task notifications + GC. OrchestratorDeps gained `task_registry`. | design: `plans/task-manager.md` |
| ToolSearch + deferred registry | ToolSearchTool (core layer, `kind=think`): 3 query modes (select/+prefix/freetext), promotes matched deferred tools to core via `ToolRegistry.promote()`. `ToolSnapshot.deferred_listing` for system-prompt injection. ToolManager auto-routes `should_defer=True` tools to deferred layer. Orchestrator Step 3a.1 injects `<system-reminder>` with deferred tool names. +28 unit tests + 3 e2e tests (1129 kernel tests total). | design: `kernel/subsystems/tools.md` § 4, § 10 phase 4 |
| `dont_ask` permission mode | 6th PermissionMode: user-initiated "only pre-approved tools execute, all ask→deny". `ReasonMode(mode="dont_ask")` tag distinguishes from system-initiated `ReasonNoPrompt`. `AmbientContext.mode` literal updated. +4 unit tests, E2E set_mode roundtrip updated (1237 tests). Closes gap with Claude Code's `dontAsk` mode. | `tool-authorizer.md`, `claude-code-coverage.md` |
| AskUserQuestion tool | Structured multi-choice questions via permission channel hijack. `PermissionResponse.updated_input` carries answers back through the permission round-trip. ACP schema extended (`PermissionOutcomeSelected.updated_input`, `RequestPermissionRequest.tool_input`). Probe client supports `updated_input` kwarg. +29 unit tests + 2 e2e tests (1277 kernel tests total). | aligned with Claude Code `AskUserQuestionTool.tsx` |
| Plan Mode CC full alignment | 14-gap alignment with Claude Code `EnterPlanModeTool/ExitPlanModeV2Tool`. New: `plans.py` (slug generation, plan file management, `~/.mustang/plans/`), `PlanUpdatedEvent`, plan file write exception in ToolAuthorizer, 5-phase workflow prompt + sparse reminder, full/sparse throttling (5 turns/5 attachments cycle), Session-layer `_set_mode` closure (writes ModeChangedEvent + broadcasts CurrentModeUpdate), `pre_plan_mode` state tracking + "restore" sentinel, `has_exited_plan_mode`/`needs_plan_mode_exit_attachment` session flags, re-entry + exit one-shot notifications, agent depth restriction, non-interactive session guard (`should_avoid_prompts` → disable EnterPlanMode), ExitPlanMode user confirmation (`default_risk=ask`) + plan content return, team approval interface reserved. +31 unit tests + 3 e2e tests. | design: plan in `.claude/plans/compressed-foraging-hopcroft.md`; CC ref: `plans.ts`, `EnterPlanModeTool.ts`, `ExitPlanModeV2Tool.ts`, `messages.ts:3207-3397`, `attachments.ts`, `permissions.ts`, `filesystem.ts` |
| SendMessage + Agent Resume + ACP 跨 Session | SendMessageTool: 3 routing paths (in-session queue, transcript resume, cross-session ACP). AgentTool `name` param + transcript capture on completion. TaskRegistry: name→id registry + message queue/drain. Orchestrator STEP 0: drain pending messages for sub-agents. SessionManager `deliver_message()` for cross-session. SubAgentEnd event carries transcript. `ToolContext.deliver_cross_session` + `OrchestratorDeps.deliver_cross_session` wiring. 17 builtin tools (was 16). +15 unit tests + 4 e2e tests (1338 tests total). | design: `kernel/subsystems/send-message.md`; CC ref: `SendMessageTool.ts`, `resumeAgent.ts`, `LocalAgentTask.tsx` |
| ScheduleManager (Cron/Monitor) | Full cron scheduling subsystem: `kernel/schedule/` package (types, store, scheduler, executor, delivery, errors, schedule_parser). CronStore (SQLite `kernel.db`, durable/non-durable dual-layer). CronScheduler (event-driven asyncio timer, multi-instance claim via `running_by` + heartbeat, startup catch-up, max_age expiry). CronExecutor (isolated session spawn, heartbeat loop, auto-approve permissions). DeliveryRouter (session/acp/gateway targets, transient retry, idempotency cache, silent pattern, failure alerts). 3 deferred tools: CronCreateTool, CronDeleteTool, CronListTool. 4 schedule formats (cron/every/at/delay). RepeatConfig (count/duration/until 3-way). 5-level exponential backoff (OpenClaw). HookEvent +2 (PRE_CRON_FIRE, POST_CRON_FIRE → 16 total). `/loop` bundled skill. 20 builtin tools (was 17). +86 unit tests + 6 e2e tests (1424 tests total). | design: `plans/schedule-manager.md`; CC ref: `ScheduleCronTool/`, `cronScheduler.ts`; OpenClaw ref: `src/cron/`; Hermes ref: `cron/` |
| BashClassifier gap closure | Compound command read-only classification (ported from daemon `bash_safety.py`), `bash_safe_commands` config field, destructive warnings in permission prompts. Closes daemon-migration gap #3 (P1). `_COMPOUND_SAFE_COMMANDS` + `_GIT_READ_ONLY` strict lists for compound safety, `_is_compound_safe()`, `destructive_warning()` on Tool base class. ToolManager injects user config via `get_section` (read-only view). +44 new tests (1617 tests total). | design: gap analysis in `daemon-migration-gaps.md` |
| GitManager (git context + worktree) | Full git subsystem: `kernel/git/` package (types, store, context, worktree, GitManager). GitManager Subsystem (startup never fails, `_available` flag). Git binary resolution (user config > PATH > unavailable). Dynamic tool registration (`_sync_tools` registers/unregisters EnterWorktree+ExitWorktree as git availability changes). ConfigManager signal subscription for hot-reload. Git context injection (5 parallel commands, session-level cache, CC format). WorktreeStore (SQLite `kernel.db`, crash-recovery GC). EnterWorktreeTool (slug validation, sparse checkout, context_modifier cwd switch). ExitWorktreeTool (keep/remove, uncommitted changes guard). context_modifier pipeline (ToolExecutor consumes + Orchestrator callback). Session resume (worktree cwd restore from DB). Worktree startup mode (ACP `_meta.worktree`). 22 builtin tools (was 20). +69 unit tests + 2 e2e tests (1493 tests total). | design: `kernel/subsystems/worktree-and-git-context.md`; CC ref: `EnterWorktreeTool.ts`, `ExitWorktreeTool.ts`, `worktree.ts`, `context.ts` |
| Phase 1 CC prompt alignment + PromptManager migration | `Tool.description_key` + PromptManager injection + `get_description()` hook (overridable for dynamic month/year); TodoWrite schema gains required `activeForm` (CC parity — imperative + present-continuous); `compact` role on `CurrentUsedConfig` with `model_for_or_default()` fallback to default; WebFetch CC-style secondary-model post-processing via `ctx.summarise` (SessionManager-wired closure); Compactor switched to compact role; 2 new HookEvents (`WORKTREE_CREATE`/`REMOVE` → 18 total) + EnterWorktree/ExitWorktree hook-based non-git path (CC parity); every built-in tool description moved to `prompts/default/tools/*.txt` (24 files) — the "tool descriptions may live in .py" exception is retired. 6 prompts rewritten to CC full text (web_search/enter_plan_mode/exit_plan_mode/exit_worktree/cron_create/todo_write) with Mustang-specific adaptations (path substitution, no-tmux, multi-backend search, cron addenda). 22 canary tests prevent drift. +~60 unit tests, probe-verified (1649 tests total). | design: `docs/plans/prompt-alignment-with-cc.md`; CC ref: `src/tools/*/prompt.ts` |
| Phase 5 language section (prompt alignment) | CC `getLanguageSection()` (prompts.ts:142-149) ported: new `OrchestratorPrefs(language: str | None)` config schema in `orchestrator/config_section.py`, bound by SessionManager against `config.yaml` `orchestrator` section; `OrchestratorConfig` + `OrchestratorConfigPatch` grow `language` field; `PromptBuilder.build()` gains `language: str | None` kwarg and injects `PromptSection(cache=True)` immediately after env context; template lives at `prompts/default/orchestrator/language.txt` with `{language}` placeholder. 13 canary tests + `scripts/probe_language.py` verifying all 3 closure seams (PromptManager render · ConfigManager→SessionManager→OrchestratorConfig · query()→provider.stream). Sub-agents inherit via parent `_config`. 1710 tests passing. | design: `docs/plans/prompt-alignment-with-cc.md` Phase 5; CC ref: `src/constants/prompts.ts:142-149,499-504` |
| Phase 4 MCP instructions (prompt alignment) | CC `getMcpInstructions()` (prompts.ts:579-604) ported: `OrchestratorDeps.mcp_instructions` sync closure field; SessionManager wires `_mcp_instructions()` closure over `MCPManager.get_connected()` (same try/KeyError/ImportError pattern as GitManager); `prompts/default/orchestrator/mcp_instructions.txt` holds CC verbatim header + intro + `{blocks}` placeholder; `PromptBuilder.build()` injects `PromptSection(cache=False)` after language section (CC: `DANGEROUS_uncachedSystemPromptSection`, servers connect/disconnect between turns); servers with empty instructions filtered out. `isMcpInstructionsDeltaEnabled()` delta-attachment branch deliberately not ported. 16 canary tests + `scripts/probe_mcp_instructions.py` verifying 2 closure seams (PromptManager render byte-equality · SessionManager→Orchestrator→provider.stream connected+degraded paths). 1740 tests passing. | design: `docs/plans/prompt-alignment-with-cc.md` Phase 4; CC ref: `src/constants/prompts.ts:579-604,513-520` |
| Kernel file-length refactor planning | Added `docs/plans/kernel-file-length-refactor-plan.md`: scanned `src/kernel/kernel/**/*.py` against code-quality limit (`<300` lines per file), identified 40 over-limit files, grouped by priority (P0/P1/P2), and drafted incremental split batches (A–E) with subsystem-boundary and API-stability constraints. This is planning-only (no runtime code moved yet). | design: `docs/plans/kernel-file-length-refactor-plan.md` |
| Session module file-length refactor | Batch A implemented for `src/kernel/kernel/session/`: `SessionManager` moved from monolithic `session/__init__.py` into a thin facade with internals grouped by functional path (`api/`, `lifecycle/`, `turns/`, `client_stream/`, `orchestration/`, `persistence/`, `runtime/`). Runtime state, flags, helpers, event schema, and spillover helpers split out while preserving `from kernel.session import SessionManager`, `kernel.session.events`, and `kernel.session.store` import paths. Follow-up simplification consolidated session creation, prompt enqueue, runtime close, config-list projection, optional-subsystem lookup, tool update emission, and replay restoration helpers. All session files are now under 300 lines. Verified with 55 session tests, 39 protocol/gateway/lifespan tests, and 5 selected Session E2E tests. | design: `docs/plans/session-module-refactor-plan.md` |
| Session ACP compliance + lifecycle actions | ACP session surface now returns `updatedAt`, `_meta`, initial `configOptions`, and `modes`; `set_mode` and `set_config_option(mode)` share validation/state updates; relative `cwd` and invalid cursors fail fast. Added user-visible `session/rename`, `session/archive`, and `session/delete`; schema v2 adds `archived_at` and `title_source`; default list hides archived sessions with `includeArchived` / `archivedOnly` filters. Inbound `$/cancel_request` cancels in-flight JSON-RPC requests with `-32800`. Non-empty session-scoped `mcpServers` now returns `InvalidParams` until MCPManager grows session-scoped registries. 118 targeted tests passing. | design: `docs/plans/session-acp-compliance-refactor.md` + `docs/plans/session-lifecycle-actions.md` |
| Orchestrator module file-length refactor | Batch C implemented for `src/kernel/kernel/orchestrator/`: public API/schema split into thin exports (`api`, `config`, `deps`, `permissions`, `stop`, `tool_kinds`), events/history/compaction moved into functional packages, `ToolExecutor` split into `tools_exec/`, and `StandardOrchestrator` reduced to a facade over query-loop, prompt, reminder, notification, hook, and sub-agent helpers. All orchestrator files are now under 300 lines while preserving package-root, `types`, `events`, `tool_executor`, `history`, `compactor`, and `orchestrator.StandardOrchestrator` import paths. Fixed the real ACP boundary for Mustang-only `ToolKind.orchestrate` by mapping it to ACP `other`. | design: `docs/plans/orchestrator-module-refactor-plan.md`; verified with 219 targeted kernel tests + 8 selected E2E tests |
| Orchestrator module readability pass | Applied `docs/workflow/readable-code.md` to `src/kernel/kernel/orchestrator/`: introduced structural runtime protocols for query-loop helpers, named turn/stream state objects, clarified permission-mode projection, removed remaining local `type: ignore` workarounds, replaced abbreviated locals, tightened tool-executor callback/dependency types, and factored reminder/prompt helpers so the split modules read by responsibility instead of implementation accident. Compatibility facades from the file-length split remain intact. | Verified: `ruff format/check src/kernel/kernel/orchestrator`; 219 targeted kernel tests; 8 selected Orchestrator E2E tests |
| Orchestrator comment pass | Filled comment/docstring gaps across `src/kernel/kernel/orchestrator/` using `docs/workflow/code-quality.md` and `docs/workflow/readable-code.md`: every class/function now passes a signature-aware AST docstring audit for required Args, Returns/Yields, and Raises sections; event/config/permission schemas explain boundary semantics; compaction and tool-execution helpers document lossy, provider-replay, authorization, and concurrency invariants. Comment density is high (50.32%) because structured docstrings are counted; over-300 physical file lengths are docstring-driven, not code-line regressions. | Verified: structured docstring AST scan; `cloc src/kernel/kernel/orchestrator --by-percent c`; `ruff format/check`; `mypy`; 178 orchestrator tests |
| Workflow readable-code guide | Added standalone `docs/workflow/readable-code.md`, adapted from the readable-code article and clean-code supplement, covering naming, formatting, comments, control flow, expressions, variables, extraction, single-job functions, plain-language design, deletion, readable tests, and consistency. `docs/workflow/code-quality.md` now links to it instead of carrying the full checklist inline. | sources: heyitao reading notes + baymaxium gist |
| Session module readability pass | Applied `docs/workflow/readable-code.md` to `src/kernel/kernel/session/`: named connection binding, list pagination, title seeding, token update, replay-state reconstruction, tool-result persistence, and plan-mode transition concepts; reduced vague locals and silent exception swallowing while preserving public `SessionManager` API and session package import paths. Closure seam touched: existing `SessionOrchestratorFactoryMixin._set_mode` closure; verified through real ACP plan-mode E2E. | Verified: `ruff format/check src/kernel/kernel/session`; 77 session/protocol/routes tests; `test_kernel_e2e.py`; `test_session_resume_e2e.py`; `test_plan_mode_e2e.py`; session comment density 22.79% |
| CLI Phase B0 active-port scaffold | Added `src/cli/active-port-manifest.json`, `scripts/check_active_port.ts`, and `scripts/copy_oh_my_pi_file.ts` to enforce the on-demand oh-my-pi TUI port boundary before any UI files are copied. `tsconfig.json` already had the required `src/**/*` + `tests/**/*` include boundary, and the checker now guards that invariant plus bulk-vendor denylist paths. | plan: `docs/cli/history/phase-b-tui-migration.md` B0 |
| Memory LLM stream contract fix | Fixed Memory selector/background LLM calls after `LLMManager.stream()` gained required `system`, `tool_schemas`, and `temperature` keyword-only args and changed to an awaitable generator factory. Added shared `memory.llm_text.collect_llm_text()` so both memory scoring and background extraction use the current typed stream contract. | Verified: `uv run pytest tests/kernel/memory -q` (86 passed); `uv run ruff check src/kernel/kernel/memory tests/kernel/memory/test_llm_text.py` |
| Permission dynamic ACP options | `ToolExecutor` now projects `PermissionAsk.suggestions` into `PermissionRequest.options`; `SessionPermissionMixin` maps those dynamic options into ACP `session/request_permission.options`, preserving the legacy 3-button default only for callers that do not provide options. Destructive asks can now hide `allow_always` end-to-end through the session permission seam. | plan: `docs/cli/history/phase-c-permissions.md` C0.5; verified with targeted kernel tests |
| AskUserQuestion text questions | Extended `AskUserQuestionTool` with `type: "text"` questions for free-form user input over the existing Mustang permission-channel extension. Choice questions remain backward-compatible (`type` omitted = `choice`, options required); text questions do not require options and may carry `placeholder`, `multiline`, and `maxLength`. Tool prompt, validation, unit tests, ToolExecutor updated_input forwarding, and ACP e2e text path updated. | plan: `docs/cli/history/phase-c-permissions.md` C4b |
| CLI Phase C permission UI | Implemented CLI-side `session/request_permission` handling: permission mapper/model/queue, fail-closed ACP default, `PermissionController` over oh-my-pi `HookSelectorComponent` / `HookInputComponent` / `HookEditorComponent`, `InteractiveMode` handler wiring, ordinary tool authorization overlay, AskUserQuestion choice/text updatedInput path, Ctrl+C overlay cancellation priority, and Phase C local test runner. Production `main.ts` no longer installs an implicit auto-allow handler. | plan: `docs/cli/history/phase-c-permissions.md`; verified with CLI typecheck, active-port check, Phase A + Phase C CLI tests, targeted kernel tests, AskUserQuestion e2e |
| CLI `!` / `$` kernel REPL | Migrated oh-my-pi user REPL behavior to kernel-side ACP requests: `session/execute_shell`, `session/execute_python`, and `session/cancel_execution`; CLI now routes `!`/`!!` shell and `$`/`$$` Python input over WebSocket instead of local execution. Shell execution reuses ToolManager primitive shell tools with shared streaming/cancel helper, adds Windows `CmdTool` fallback, and Python uses a per-session worker process with persistent namespace. | plan: `docs/cli/history/kernel-repl-bang-dollar.md`; verified with 1766 kernel tests, py_compile, ruff targeted, CLI node syntax checks, and real FastAPI ACP WS probes for shell/python/cancel/context |
| Cron session reaper accounting fix | `SessionStore.delete_session()` now returns whether the session row actually existed, and `SessionManager.delete_session()` propagates that result so the cron reaper no longer logs repeated successful deletes for already-missing cron execution sessions while stale `cron_executions` audit rows remain. Added regression tests for missing-session deletion and reaper accounting. | verified with targeted session/schedule tests, ruff, mypy |
| CLI Phase D — session/config/theme | Implemented ACP-backed session creation/switching and lifecycle actions in the CLI, typed `~/.mustang/client.yaml` config with env/argv precedence, token-file/literal/env token resolution, startup orchestration, Welcome recent sessions, `/session` list/switch commands, theme config + `/theme`, and opt-in loopback-only kernel autostart. UX revision: default startup creates a new session immediately; historical session switching happens inside the TUI. The CLI boundary remains WebSocket ACP only: no kernel Python imports, no direct SQLite/state/sidecar access. | plan: `docs/cli/history/phase-d-session-config-theme.md`; verified with CLI typecheck, Phase D local tests, active-port manifest check, Phase A smoke against temporary kernel, and real ACP lifecycle probe |
| Session lifecycle actions planning | Added `docs/plans/session-lifecycle-actions.md`: kernel-side plan for user-visible `session/delete`, `session/rename`, and `session/archive`/unarchive ACP extensions, including semantics, schema/version impact, implementation batches, tests, and closure-seam probes. | planning only; no runtime code changed |
| Session ACP compliance refactor planning | Added `docs/plans/session-acp-compliance-refactor.md`, collecting ACP session gaps found against the local spec mirror: `SessionInfo.updatedAt`, `session_info_update.updatedAt`, config option descriptors, initial modes/config state, mode validation, per-session MCP servers, cwd/cursor validation, optional `$/cancel_request`, and lifecycle actions. | planning only; no runtime code changed |
| CLI Phase B UI alignment repair planning | Added `docs/cli/history/phase-b-ui-alignment-repair.md` after reviewing the current CLI against the original Phase B parity goal. The plan classifies the current TUI as a partial first-usable path, not full oh-my-pi parity, and defines repair batches for status-line restoration, ACP adapter isolation, copied `InteractiveMode` activation, editor/input parity, golden render tests, and real CLI PTY/TUI E2E probes. | planning only; no runtime code changed |
| CLI Phase B UI alignment first repair slice | Replaced the 48-line status-line visual stub with an editor top-border renderer carrying model/path/context/mode segments; added ACP-backed `ModelService` for `model/profile_list` and `model/set_default`; startup now renders the no-model warning above the main input area; `/session`, `/model`, `/theme`, and `/plan` slash arguments now feed the copied autocomplete/select-list path, including `/session info/delete` and model profile completions; added Phase B local tests for status line and autocomplete. Full upstream `InteractiveMode` adapter isolation, exhaustive golden matrix, and PTY probe remain open in the repair plan. | plan: `docs/cli/history/phase-b-ui-alignment-repair.md`; verified with CLI typecheck, active-port check, Phase A smoke, Phase B local tests, Phase C and Phase D local tests |
| CLI Phase B UI alignment R1/R2/R3 | Completed the main-path repair batches requested after the first slice: copied upstream OMP status line restored; copied OMP `InteractiveMode`, `InputController`, `CommandController`, `EventController`, and `SelectorController` are dynamically loaded on the production wrapper path; added `MustangAgentSessionAdapter` to isolate ACP `sessionUpdate` handling and translate streaming text/thinking/tool/session events into OMP-style session state/events. Heavy OMP selector sub-UIs without Mustang backing services are explicit dependency stubs. R5/R6 were later completed in the rows below. | plan: `docs/cli/history/phase-b-ui-alignment-repair.md`; verified with CLI typecheck, active-port check, Phase B/C/D local tests, CLI run_all smoke |
| CLI Phase B UI alignment R4 | Wired copied OMP input submit/key handling to Mustang behavior: `!`/`!!` and `$`/`$$` continue through kernel ACP execution methods via the adapter, copied Escape/Ctrl+C paths are covered locally, and the active-port builtin slash registry now handles Mustang `/session`, `/model`, and `/theme` commands instead of falling through as prompts. `/session delete` keeps the explicit `confirm` guard before calling the ACP delete/create path. R5/R6 were later completed in the rows below. | plan: `docs/cli/history/phase-b-ui-alignment-repair.md`; verified with CLI typecheck, active-port check, Phase B/C/D local tests, CLI run_all smoke |
| CLI Phase B UI alignment R5 | Added deterministic UI golden-frame coverage to Phase B: Welcome first screen, status/editor border with short and multiline input, `/session` autocomplete rows and selected state, no-model warning with autocomplete, assistant thinking/markdown, bash running/completed, generic tool pending/completed/failed, and permission selector overlay. Frames are ANSI-stripped and checked for stable structure/content plus width fit. R6 was later completed via the PTY/TUI probe below. | plan: `docs/cli/history/phase-b-ui-alignment-repair.md`; verified with CLI typecheck and Phase B local tests |
| CLI Phase B UI alignment R6 | Added real CLI PTY/TUI E2E probe (`probe_phase_b_pty.ts`). The probe starts a fake ACP WebSocket kernel, launches the real CLI in a pseudo-terminal, sends real key input, captures terminal output, and verifies first viewport/no-model warning, slash autocomplete, `!` shell, `$` python, tool rendering, `/session delete` confirm guard and confirmed delete, permission overlay, plus ACP closure calls (`session/execute_shell`, `session/execute_python`, `session/delete`, `session/prompt`, permission response). | plan: `docs/cli/history/phase-b-ui-alignment-repair.md`; verified with CLI typecheck and PTY probe |
| CLI async event ordering fix | Fixed `MustangAgentSessionAdapter` so ACP streaming updates are queued through async TUI listeners and flushed before `message_end` / `agent_end`. This prevents final assistant chunks from being dropped when tool rendering is still processing and the prompt response has already returned. Added a delayed-listener regression test. | verified with `bun run tests/test_agent_session_adapter.ts`, `bun run src/cli/tests/run_all.ts`, and `bun run src/cli/tests/probe_phase_b_pty.ts` |
| CLI permission OMP hook-dialog alignment | Replaced the production permission prompt's bespoke bottom overlay path with the copied OMP hook-dialog lifecycle: `ExtensionUiController.showHookSelector()` / `showHookInput()` / `showHookEditor()` now mount into `editorContainer`, focus the hook component, and restore the editor on close; `PermissionController` uses that host in the copied `InteractiveMode` path while retaining the legacy TUI fallback. | verified with CLI typecheck, active-port check, Phase B/C local tests, `run_all`, and the real CLI PTY/TUI permission probe |
| CLI session-list rendering fix | Fixed active-port `/session list` so recent sessions render as a chat block instead of a multiline status message that collides with the editor/status line. The command now reuses `renderSessionPicker()` and caches the last list so `/session switch <number>` resolves to the selected ACP session id. | verified with `bun run tests/test_input_controller_r4.ts`, `bun run tests/test_session_picker.ts`, `bun run src/cli/tests/run_all.ts`, active-port manifest, and PTY probe with `/session list` |
| CLI assistant/tool ordering fix | Fixed OMP event-controller adaptation so an empty assistant streaming component is not mounted at turn start. Assistant text/thinking now mounts only when visible content arrives, preserving the natural order when tools run first and the final answer streams afterward. PTY probe now asserts tool output precedes the final assistant text. | verified with `bun run src/cli/tests/probe_phase_b_pty.ts`, `bun run tests/test_ui_golden_r5.ts`, `bun run tests/test_agent_session_adapter.ts`, and CLI local suite |
| CLI OMP-first refactor planning | Added `docs/cli/history/omp-first-refactor.md`, then translated it to Chinese for review. B1 is resolved locally: `.mustang-refs.yaml` now registers `oh-my-pi`, `.mustang-refs.example.yaml` documents the template entry, and the plan records `/home/saki/Documents/alex/oh-my-pi` at baseline commit `c73c18a1fb3e2f2225ca685f290ec67d326689bf`. Remaining blockers are OMP file-backed sessions vs Mustang ACP sessions, wider OMP service surface, active-port ownership, and dirty worktree/write-set separation. Batches R0-R6 cover reference inventory, adapter contract, session selector, permission dialog, controller diff cleanup, parity tests, and deletion of bespoke UI paths. | planning only; B1 reference registration complete; no runtime code changed |
| CLI OMP-first refactor implementation | Implemented the OMP-first CLI reset from `docs/cli/history/omp-first-refactor.md`: restored OMP `SessionSelectorComponent`, added ACP-backed OMP `SessionInfo` provider in the active-port `session-manager` seam, routed `/session list` through the OMP selector, kept `/session switch <number>` resolving via ACP session list, routed selector deletion through ACP when available, added `check_omp_parity.ts` to enforce strict upstream parity for copied files and document allowed seams, added OMP session selector coverage, extended the PTY probe for selector ownership, and removed the old Mustang TUI implementation from `src/cli/src/modes/interactive.ts` so production starts through copied OMP `InteractiveMode`. | verified with OMP parity check, active-port manifest, CLI typecheck, Phase B/C tests, permission host test, agent adapter tests, and real CLI PTY probe |
| CLI completed-tool replay fix | Fixed the remaining tool/answer rendering drift where a completed tool could be recreated as a stale `pending <tool>` below the final assistant answer. The OMP event-controller seam now tracks completed tool call ids and ignores replayed `toolCall` blocks from later full-message assistant updates. The PTY probe asserts that no pending tool appears after the final answer marker. | verified with Phase B/C tests, active-port manifest, OMP parity check, CLI typecheck, and real CLI PTY probe |
| CLI active-port import and tool-order correction | Removed the remaining `@oh-my-pi/*` import namespace from CLI source/tests by renaming compat shims to Mustang-owned module names and routing imports through `@/compat/*` / `@/tui/index.js`. Corrected the OMP event-controller adaptation to preserve first-created block ordering: `message_update` now creates/updates tool blocks from earlier `toolCall` content before mounting visible assistant text, while `tool_call_update` only mutates the existing block. PTY coverage now rejects both stale pending and stale success tool blocks after the answer. | audit: `docs/plans/cli-active-port-prune-audit.md`; verified with CLI typecheck, CLI local suite, and real CLI PTY probe |
| CLI docs reorganization | Split CLI docs by state: `docs/cli/` now holds implemented design facts and history, while unfinished CLI work (`cli-plan`, active-port prune audit, keybinding gap) lives under global `docs/plans/`. Removed the extra CLI-local roadmap to keep `docs/plans/roadmap.md` as the shared roadmap entry. | docs-only; verified by stale-link grep and `git diff --check` |

## Summaries

### ToolAuthorizer M3 — HookManager fire-sites + LLMJudge real impl

Built on top of the HookManager skeleton (14-event enum, `fire(ctx)`
API, `HookBlock` exception, EVENT_SPECS matrix, user/project
directory discovery, boundary safety).  M3 filled in the fire sites
that the M2 commits left as TODOs.

**Tool lifecycle fire sites** (in `orchestrator/tool_executor.py`):
- ``pre_tool_use`` — fired between authorize-allow and tool.call; a
  handler can raise ``HookBlock`` to abort (yielded as
  ``ToolCallError``) or mutate ``ctx.tool_input`` to rewrite the
  effective input before execution.
- ``post_tool_use`` — fired after a successful call with the final
  ``tool_output`` string; observer-only.
- ``post_tool_failure`` — fired when tool.call raises; carries
  ``error_message``; observer-only.
- Every fire drains ``ctx.messages`` into ``Session.pending_reminders``
  via ``OrchestratorDeps.queue_reminders`` (closure wired by
  SessionManager).

**Permission fire sites** (in `tool_authz/authorizer.py`):
- ``permission_denied`` — fired after the authorize call produces a
  ``PermissionDeny`` (any path: rule, mode=plan, fail-closed, LLMJudge
  unsafe, etc.).  Observer-only.
- ``permission_requested`` — fired when a ``PermissionAsk`` reaches
  the caller.  Observer-only.
- Hook fire failures are caught and logged — they never corrupt the
  authorize decision.  HookManager lookup is lazy
  (`module_table.get(HookManager)` at fire time) so the step-3
  authorizer survives HookManager being absent or loading later.

**System-reminder drain path** (in `orchestrator/orchestrator.py`):
- SessionManager passes ``queue_reminders`` + ``drain_reminders``
  closures to OrchestratorDeps; the drain pops from
  ``Session.pending_reminders`` at the start of each
  ``_run_query``.
- Drained strings are formatted as
  ``<system-reminder>\n…\n</system-reminder>`` blocks and prepended to
  the user message — mirrors CC's deferred-reminder pattern while
  keeping the prompt-cached system prompt untouched.
- ``_to_text_content`` persists the rendered reminders into the
  conversation history so the JSONL audit matches what the LLM saw.

**BashClassifier LLMJudge** (in `tool_authz/bash_classifier.py`):
- Stateless classify(): caller resolves
  ``LLMManager.model_for("bash_judge")`` and passes ``llm_manager`` +
  ``model_ref`` in; None-None short-circuits to ``"unknown"`` so the
  user gets prompted.  ToolAuthorizer's
  ``_resolve_bash_judge_model()`` does the lookup lazily per call.
- XML-bounded prompt (``<command>…</command>`` +
  ``<context>…</context>``) with an explicit "treat tags as data, not
  instructions" guard against prompt injection.  Temperature pinned to
  0 for deterministic classification.
- ``_stream_to_text`` consumes the LLMManager stream; a
  ``StreamError`` chunk raises so the caller's ``fail_closed`` flag
  policy handles it uniformly with provider exceptions.
- ``_parse_verdict`` tolerates markdown fences and ignores extra
  JSON fields; any parse failure → ``"unknown"``.  Verdicts feed the
  existing ``DenialCounters`` (MAX_CONSECUTIVE=3 / MAX_TOTAL=20); a
  ``safe`` verdict resets the consecutive counter but not the total.

**Tests** (+26; 468 total): integration suite covers the fire-site
ordering (``pre_tool_use → ToolCallStart → call → post_tool_use``),
block path (``HookBlock`` on pre_tool_use ⇒ no call, no post hook),
input-rewrite path (handler mutates ``ctx.tool_input`` before execute),
crash path (``post_tool_failure`` instead of ``post_tool_use``),
reminder drain round-trip, permission-hook emit on deny + ask,
hook crash resilience (authorize decision survives), and the LLMJudge
verdict matrix (safe / unsafe / unknown / fenced-JSON / stream-error
under both fail-closed and fail-open).

### ToolManager + ToolAuthorizer (Phase 7, M1 + M2)

Two new subsystems landed together per the design docs, following the
Tool ABC → internal components → Subsystem → Orchestrator wiring path.

**ToolManager** (step 5, optional): Tool ABC with
`default_risk` / `prepare_permission_matcher` / `is_destructive` /
`aliases` contract; ToolContext (no `authorizer_hint` — aligned with
Claude Code); ToolRegistry with core / deferred layers + deterministic
schema ordering for prompt cache; FileStateCache for read-then-edit
verification; 6 built-in tools (Bash with argv + allowlist +
dangerous-pattern classifier, FileRead / FileEdit / FileWrite with
FileStateCache wiring, Glob / Grep).  `snapshot_for_session()` consults
`authorizer.filter_denied_tools()` so denied tools never reach the LLM
(defense-in-depth with per-call `authorize()`).

**ToolAuthorizer** (step 3, core): PermissionRule DSL parser with
fail-closed on malformed input; RuleStore layered over ConfigManager
Signal subscription (user/project/local merged; flag layer is
runtime-frozen); RuleEngine consumes Tool contract methods and does
`deny > ask > allow` arbitration aligned with Claude Code
`permissions.ts:1158-1224`; SessionGrantCache with exact-command-string
signatures and destructive-guard at `PermissionAsk.suggestions` build
time; BashClassifier with denial tracking (maxConsecutive=3 /
maxTotal=20, aligned with CC `denialTracking.ts`) — LLMJudge call itself
is a stub wired for M2c.  Hook events defined (`permission_denied` /
`permission_requested`) but HookManager integration deferred to M3.

**Orchestrator integration**: `ToolExecutor` upgraded to streaming-shaped
interface (`add_tool` / `finalize_stream` / `results()` / `discard()`)
with parallel batch execution.  Seven-step per-tool pipeline unchanged
(validate_input → authorize → pre_tool_use → call → post_tool_use →
emit result).  Consecutive `is_concurrency_safe` tools are partitioned
into parallel batches; non-safe tools run in singleton batches.
`asyncio.Lock` serializes `on_permission` prompts across concurrent
tools.  Orchestrator creates a per-turn `ToolExecutor` with
`streaming=config.streaming_tools`; `streaming_tools=True` feeds
`ToolUseChunk` blocks to `add_tool()` during the LLM stream (safe tools
start immediately), `False` (default) queues all tool_uses and dispatches
after stream ends.  Legacy `run()` wrapper preserved for backward
compatibility.  `OrchestratorDeps` gained `tool_source`, `authorizer`,
`connection_auth`, `should_avoid_prompts_provider` fields.
`should_avoid_prompts_provider` is a `lambda: no active senders` closure
wired in SessionManager — implements Option C (dynamic based on
interactive channel availability, sub-agents inherit root session state)
from the tool-authorizer design doc § 4.2.

**Tests**: 63 new unit tests across `tests/kernel/tool_authz/` and
`tests/kernel/tools/` covering DSL parsing escape cases, decision flow
branches (grant short-circuit, plan mode, bypass, should_avoid_prompts,
destructive tool excludes allow_always), session-scoped grant cache,
denial tracking, registry snapshot filtering, FileStateCache
invalidation, Bash classifier + end-to-end subprocess execution, and
ToolManager startup integration.

### Kernel bootstrap + LLM + Protocol + Session + Orchestrator

Full rewrite of `src/daemon/` as `src/kernel/`.  Clean subsystem
boundaries, ACP protocol, Pydantic v2 throughout, layered config,
unified ConnectionAuthenticator (connection AuthN subsystem, renamed
from AuthManager in both docs and code on 2026-04-16; package
`kernel.auth` → `kernel.connection_auth` on 2026-04-16 to keep
the AuthN module visually distinct from the forthcoming
`tool_authz` package; see
`docs/kernel/subsystems/connection_authenticator.md`), Provider ABC
pattern for LLM backends.
SessionManager with JSONL persistence and multi-connection broadcast.
StandardOrchestrator with history, compaction, and tool execution loop.

### Session Storage → SQLite

Replaced the dual-write JSONL + `index.json` model with a single SQLite
database (`sessions.db`) under `~/.mustang/sessions/`.  Key changes:

- New `session/models.py`: `ConversationRecord` ORM mapped class,
  `session_events` Core Table, `TokenUsageUpdate` dataclass.
- New `session/migrations.py`: auto-migration on every startup via
  `PRAGMA user_version`; guards against future-version DBs; each
  migration runs atomically with its version bump.
- Rewritten `session/store.py`: async SQLAlchemy 2.0 + aiosqlite;
  all mutations are single-transaction (atomic).  WAL mode for concurrency.
  `open()` calls `migrations.apply()` — fully hands-free for users.
- `TurnCompletedEvent` gains `input_tokens` / `output_tokens` per-turn fields.
- `StandardOrchestrator` accumulates per-turn token usage and exposes it via
  `last_turn_usage` property; `Orchestrator` Protocol updated accordingly.
- `SubAgentSpawnedEvent.subagent_file` removed; sub-agent events now share
  the `session_events` table, distinguished by `agent_depth > 0`.
- `SessionManager` removes `_index`, `_index_lock`, `Session.jsonl_path`,
  `Session.write_lock`; all reads/writes go through `SessionStore`.
- 39 tests: `test_store.py` (unit) + `test_session_manager.py` (integration)
  + `test_migrations.py` (migration engine), all passing.

Note: existing JSONL data is not migrated.  New installs start fresh.

### CommandManager + GatewayManager (Phase 6)

Two new trailing-core subsystems (#10 and #11), both gated by `KernelFlags`.

**CommandManager**: pure catalog — `CommandDef` dataclass, `CommandRegistry`,
and 7 built-in commands (`/help`, `/model`, `/plan`, `/compact`, `/session`,
`/cost`, `/memory`).  No `dispatch()` by design; WS clients self-dispatch
via existing ACP methods.

**GatewayManager**: manages external messaging platform adapters.  Key design
points:
- `GatewayAdapter` ABC owns the full message round-trip: receive → session →
  orchestrator → reply.
- `DiscordAdapter`: outbound Gateway WS (via `DiscordGateway`), self-message
  filter, 2000-char chunked `send()`.
- Permission model: `on_permission` sends a yes/no prompt to the platform user;
  user reply intercepted before next turn and resolves the pending `Future`.
- Per-session `asyncio.Lock` serialises session creation; lock released before
  turn runs (prevents deadlock with permission replies).
- `run_turn_for_gateway` uses new `_get_or_load` helper (vs `_get_or_raise`)
  to transparently reload evicted-but-persisted sessions.
- `websockets` added to kernel dependencies.
- 30 new tests (`test_command_manager.py` + `test_gateway_adapter.py`).
- Lifespan tests updated: 9→11 subsystems, trailing list renamed to
  `_TRAILING_SUBSYSTEMS`, expected start/stop order updated.

---

## Phase 9 — MemoryManager

**Date**: 2026-04-20
**Plan**: [kernel/subsystems/memory/design.md](../kernel/subsystems/memory/design.md)
**Design**: [kernel/subsystems/memory/design.md](../kernel/subsystems/memory/design.md)
**Research**: [kernel/subsystems/memory/research.md](../kernel/subsystems/memory/research.md)

Implemented the full MemoryManager subsystem based on research of 13
memory system projects (3 reference implementations + 10 academic/OSS
architectures).

### New modules (7 files + 4 prompts)
- `memory/types.py` — MemoryHeader, MemoryEntry, ScoredMemory, MemoryProvider Protocol, Hotness
- `memory/store.py` — atomic write (temp→os.replace+fcntl.flock), YAML frontmatter, injection scan
- `memory/index.py` — in-memory cache, hotness computation (MemU formula + OpenViking thresholds)
- `memory/selector.py` — BM25 (jieba CJK) + LLM scoring + ranking formula + alias mapping
- `memory/tools.py` — 5 tools: memory_write/append/delete/list/search
- `memory/background.py` — 3-layer extraction (direct > pre-compact > periodic) + consolidation
- `memory/__init__.py` — MemoryManager assembly, MemoryProvider protocol implementation
- `memory/prompts/` — selection.txt, extraction.txt, consolidation.txt, memory_strategy.txt

### Wiring changes
- `session/__init__.py` — added MemoryManager fetch + OrchestratorDeps(memory=...) injection
- `orchestrator/prompt_builder.py` — replaced TODO stub with Channel A (index) + Channel C (strategy)

### Key design decisions
- 4-category cognitive taxonomy: profile/semantic/episodic/procedural (from Hindsight)
- Ranking: `llm_relevance × log(access+2) × time_decay × source_weight` (from MemU benchmark)
- Hot/warm/cold: OpenViking thresholds 0.6/0.2
- Evergreen: profile/semantic/procedural immune to time decay (from OpenClaw)
- Source weights: user=1.0, agent=0.8, extracted=0.6 (from Second-Me, immutable)
- No auto-delete: decay only affects ranking (from MemU + OpenViking consensus)
- Staleness caveat: only episodic + age>7d (not evergreen categories)
- CJK: jieba segmentation for BM25 Chinese support
- Memory model: optional separate LLM for scoring/extraction, fallback to default

### Tests
- 85 new unit tests across 6 test files
- All 1015 existing tests continue to pass

### New dependency
- jieba==0.42.1 (pure Python CJK tokenizer)

---

## CLI Phase B — TUI Active Port

**Date**: 2026-04-27
**Plan**: [phase-b-tui-migration.md](../cli/history/phase-b-tui-migration.md)

Implemented the first usable Phase B TUI path for `src/run-cli.sh`.

### Delivered
- Active-ported oh-my-pi `packages/tui/src/**` and the needed coding-agent TUI
  surface into `src/cli/src/active-port/**` with a manifest checker.
- Preserved oh-my-pi directory shape for the copied TUI and coding-agent TUI
  component paths under `src/cli/src/active-port/`.
- Added local compat facades for `@oh-my-pi/pi-tui`, `pi-utils`, `pi-natives`,
  `pi-ai`, and `pi-agent-core`.
- Replaced the Phase A readline entry with a TUI `InteractiveMode`.
- `src/run-cli.sh` now starts the TUI, renders the oh-my-pi style welcome,
  editor, status line, assistant markdown/thinking, and tool execution component
  path.
- ACP `agent_message_chunk`, `agent_thought_chunk`, `tool_call`,
  `tool_call_update`, `current_mode_update`, `session_info_update`, and
  `available_commands_update` are mapped into TUI state.
- Slash autocomplete is seeded with built-in commands at startup and refreshed
  from `available_commands_update`; `/help` is handled locally instead of being
  sent to the agent as a skill prompt.

### Verification
- `bunx tsc -p src/cli/tsconfig.json --noEmit`
- `bun run src/cli/scripts/check_active_port.ts`
- `bun tests/run_all.ts` from `src/cli`: 4 passed, 0 failed
- PTY probe: `src/run-cli.sh`, submit `say hi`, assistant text streamed into
  the TUI and status returned from `running` to `ready`.
- PTY probe: type `/`; autocomplete menu opens with `/help`, `/model`,
  `/plan`, etc. Pressing Enter on `/help` renders local command help.
