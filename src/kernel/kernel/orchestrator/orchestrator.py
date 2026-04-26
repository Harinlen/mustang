"""StandardOrchestrator — real implementation of the Orchestrator Protocol.

Drives the LLM ↔ tool execution loop for one Session.  Holds conversation
history in memory; the Session layer handles JSONL persistence separately.

Phase 1 wiring
--------------
- ``deps.provider``    — required, used for every LLM call.
- ``deps.tool_source`` — None → empty tool_schemas → LLM won't call tools.
- ``deps.memory``      — None → no memory injection in system prompt.
- ``deps.skills``      — None → no skill injection in system prompt.
- ``deps.hooks``       — None → hook fire-points are skipped.

All None-dep call-sites are marked with ``# TODO`` so they are easy to
find when the corresponding subsystem is implemented.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, Literal

from kernel.llm.types import (
    PromptSection,
    StreamError,
    TextChunk,
    TextContent,
    ThoughtChunk,
    ToolResultContent,
    ToolSchema,
    ToolUseChunk,
    ToolUseContent,
    UsageChunk,
)
from kernel.llm_provider.errors import MediaSizeError, PromptTooLongError, ProviderError
from kernel.orchestrator import OrchestratorConfig, OrchestratorConfigPatch
from kernel.orchestrator.compactor import Compactor
from kernel.hooks.types import AmbientContext, HookEvent, HookEventCtx
from kernel.orchestrator.events import (
    CancelledEvent,
    CompactionEvent,
    HistoryAppend,
    HistorySnapshot,
    OrchestratorEvent,
    QueryError,
    TextDelta,
    ThoughtDelta,
    ToolCallStart,
    UserPromptBlocked,
)
from kernel.orchestrator.history import ConversationHistory
from kernel.orchestrator.prompt_builder import PromptBuilder
from kernel.orchestrator.tool_executor import ToolExecutor
from kernel.orchestrator.types import (
    OrchestratorDeps,
    PermissionCallback,
    StopReason,
)

logger = logging.getLogger(__name__)


def _dump_system_prompt(
    sections: list[PromptSection],
    session_id: str,
    model: object,
) -> None:
    """Write the system prompt text to MUSTANG_DUMP_SYSTEM_PROMPT path.

    Writes the exact concatenated text that the LLM provider sends as the
    ``system`` parameter — each section's ``.text`` joined by a separator
    line, matching what Anthropic (and other providers) receive verbatim.

    No-op when the env var is unset.  Called once per query on Turn 1.
    """
    dest = os.environ.get("MUSTANG_DUMP_SYSTEM_PROMPT", "")
    if not dest:
        return
    header = (
        f"Turn 1 | {time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())}"
        f" | model={model}\n"
        f"\n"
        f"---\n"
        f"\n"
    )
    body = "\n\n".join(sec.text for sec in sections)
    try:
        Path(dest).write_text(header + body, encoding="utf-8")
        logger.info("System prompt dumped to %s (%d sections)", dest, len(sections))
    except OSError:
        logger.warning("MUSTANG_DUMP_SYSTEM_PROMPT: could not write to %s", dest)


# Sub-agent default turn limit.  0 = unlimited (root session default).
# Internal callers (compact, memory, etc.) should pass an explicit value.
_SUBAGENT_DEFAULT_MAX_TURNS = 200

# Compaction is triggered when token_count exceeds this fraction of the
# model's context window.  80 % gives enough headroom for the next reply.
_COMPACTION_FRACTION = 0.80

# Default context window assumption when the provider does not report one.
_DEFAULT_CONTEXT_WINDOW = 200_000

# How many times to retry after PromptTooLongError before giving up.
_MAX_REACTIVE_RETRIES = 2

# max_output_tokens escalation (CC: MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3).
_MAX_OUTPUT_TOKEN_RETRIES = 3
_MAX_TOKENS_ESCALATED = 64_000


class StandardOrchestrator:
    """Full implementation of the Orchestrator Protocol.

    One instance per Session; lives as long as the Session does.

    Args:
        deps: All external dependencies.  Only ``provider`` must be non-None.
        session_id: Used in log messages and passed to sub-agents.
        initial_history: Pre-populated messages for session resume.
        config: Initial user-visible config.  Defaults to provider's default.
        depth: Sub-agent nesting depth.  0 = root agent.
    """

    def __init__(
        self,
        deps: OrchestratorDeps,
        session_id: str,
        initial_history: list[Any] | None = None,
        config: OrchestratorConfig | None = None,
        depth: int = 0,
        cwd: Path | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._deps = deps
        self._session_id = session_id
        self._depth = depth
        self._cwd = cwd or Path.cwd()
        self._agent_id = agent_id
        self._closed = False

        # Narrow LLMProvider Protocol only mandates stream(); we probe for
        # the richer LLMManager API to pick an initial model when the
        # caller did not pin one in ``config``.
        provider_model_for = getattr(deps.provider, "model_for", None)
        from kernel.llm.config import ModelRef

        default_model: ModelRef = (
            provider_model_for("default")
            if callable(provider_model_for)
            else ModelRef(provider="default", model="default")
        )
        self._config: OrchestratorConfig = config or OrchestratorConfig(
            model=default_model,
            temperature=None,
        )
        self._mode: Literal[
            "default", "plan", "bypass", "accept_edits", "auto", "dont_ask"
        ] = "default"
        self._stop_reason: StopReason = StopReason.end_turn  # updated by _run_query

        # Plan mode state (Gap 6/7/8/9).
        # Throttling counters (CC: TURNS_BETWEEN_ATTACHMENTS=5, FULL=every 5).
        self._plan_mode_turn_count: int = 0
        self._plan_mode_attachment_count: int = 0
        # Flags mirrored from Session by the _set_mode closure.
        self._has_exited_plan_mode: bool = False
        self._needs_plan_mode_exit_attachment: bool = False

        # Accumulated token usage for the current / most-recent query() call.
        # Reset at the start of each _run_query; read by the Session layer
        # after the async-for loop to populate TurnCompletedEvent + DB deltas.
        self._turn_input_tokens: int = 0
        self._turn_output_tokens: int = 0

        self._history = ConversationHistory(initial_messages=initial_history)
        self._prompt_builder = PromptBuilder(session_id=session_id, deps=deps)
        self._compactor = Compactor(
            deps=deps,
            model=self._config.model,
            keep_recent_turns=5,
        )

    # ------------------------------------------------------------------
    # Orchestrator Protocol — core
    # ------------------------------------------------------------------

    def query(
        self,
        prompt: list[Any],
        *,
        on_permission: PermissionCallback,
        token_budget: int | None = None,
        max_turns: int = 0,
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        """Start a query turn.  Returns an async generator of events."""
        return self._run_query(
            prompt,
            on_permission=on_permission,
            token_budget=token_budget,
            max_turns=max_turns,
        )

    async def close(self) -> None:
        """Release resources.  Idempotent."""
        self._closed = True
        logger.debug("Orchestrator[%s]: closed", self._session_id)

    # ------------------------------------------------------------------
    # Orchestrator Protocol — state mutation
    # ------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        """Switch permission mode (``default`` / ``plan`` / ``bypass`` / ``accept_edits`` / ``auto`` / ``dont_ask``)."""
        valid: tuple[str, ...] = (
            "default", "plan", "bypass", "accept_edits", "auto", "dont_ask",
        )
        if mode not in valid:
            raise ValueError(f"Unknown mode: {mode!r}")
        self._mode = mode  # type: ignore[assignment]

    def set_plan_mode(self, enabled: bool) -> None:
        """Backward compat for EnterPlanMode/ExitPlanMode tools."""
        self._mode = "plan" if enabled else "default"

    def set_config(self, patch: OrchestratorConfigPatch) -> None:
        self._config = OrchestratorConfig(
            model=patch.model if patch.model is not None else self._config.model,
            temperature=patch.temperature
            if patch.temperature is not None
            else self._config.temperature,
            streaming_tools=patch.streaming_tools
            if patch.streaming_tools is not None
            else self._config.streaming_tools,
            language=patch.language if patch.language is not None else self._config.language,
        )
        self._compactor = Compactor(deps=self._deps, model=self._config.model)

    # ------------------------------------------------------------------
    # Orchestrator Protocol — state reads
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        """Current permission mode string."""
        return self._mode

    @property
    def plan_mode(self) -> bool:
        """Backward compat — True when mode is ``"plan"``."""
        return self._mode == "plan"

    @property
    def config(self) -> OrchestratorConfig:
        return self._config

    @property
    def stop_reason(self) -> StopReason:
        """The stop reason from the most recent ``query()`` call.

        Only meaningful after the ``async for`` loop over ``query()`` has
        finished.  Defaults to ``end_turn`` before any query is made.
        """
        return self._stop_reason

    @property
    def last_turn_usage(self) -> tuple[int, int]:
        """``(input_tokens, output_tokens)`` accumulated during the last turn.

        Accumulates across all LLM calls within a single ``query()`` call
        (the tool loop may trigger multiple streaming requests).  Reset to
        ``(0, 0)`` at the start of each new ``query()`` call.  Safe to read
        after the ``async for`` loop completes.
        """
        return (self._turn_input_tokens, self._turn_output_tokens)

    # ------------------------------------------------------------------
    # Session-specific guidance (CC: getSessionSpecificGuidanceSection)
    # ------------------------------------------------------------------

    def _build_session_guidance(self, enabled_tools: set[str], has_skills: bool) -> str | None:
        """Build the session-specific guidance section dynamically.

        Mirrors CC's ``getSessionSpecificGuidanceSection()`` (prompts.ts:352-400).
        Each bullet is conditionally included based on which tools are
        actually available this turn.  Bullet text lives in
        ``prompts/default/orchestrator/session_guidance/*.txt`` — one
        file per conditional so individual bullets stay auditable
        against CC's source.
        """
        prompts = self._deps.prompts
        if prompts is None:
            return None

        def _get(key: str) -> str | None:
            full = f"orchestrator/session_guidance/{key}"
            return prompts.get(full) if prompts.has(full) else None

        items: list[str] = []

        if "AskUserQuestion" in enabled_tools:
            bullet = _get("deny_ask")
            if bullet:
                items.append(bullet)

        # Interactive session hint (CC gates on !getIsNonInteractiveSession())
        # Mustang sessions are always interactive for now.
        bullet = _get("interactive_shell")
        if bullet:
            items.append(bullet)

        if "Agent" in enabled_tools:
            for key in ("agent_tool", "search_direct", "search_explore_agent"):
                bullet = _get(key)
                if bullet:
                    items.append(bullet)

        if has_skills and "Skill" in enabled_tools:
            bullet = _get("skill_invoke")
            if bullet:
                items.append(bullet)

        if not items:
            return None
        bullets = "\n".join(f" - {item}" for item in items)
        return f"# Session-specific guidance\n{bullets}"

    def _inject_session_guidance(
        self, system_prompt: list[PromptSection], snapshot_tool_names: set[str]
    ) -> None:
        """Append session-specific guidance based on available tools."""
        has_skills = self._deps.skills is not None and bool(self._deps.skills.get_skill_listing())
        text = self._build_session_guidance(snapshot_tool_names, has_skills)
        if text is not None:
            system_prompt.append(PromptSection(text=text, cache=False))

    # ------------------------------------------------------------------
    # Plan mode prompt injection (Gap 5/6/8/9)
    # ------------------------------------------------------------------

    # Aligned with CC: TURNS_BETWEEN_ATTACHMENTS=5, FULL_REMINDER_EVERY_N_ATTACHMENTS=5.
    _TURNS_BETWEEN_ATTACHMENTS = 5
    _FULL_REMINDER_EVERY_N = 5

    def _inject_plan_mode_prompts(self, system_prompt: list[PromptSection]) -> None:
        """Append plan-mode / exit / reentry reminders to *system_prompt*."""
        prompts = self._deps.prompts
        if prompts is None:
            return

        # --- Gap 9: one-shot exit notification ---
        if not self.plan_mode:
            if getattr(self, "_needs_plan_mode_exit_attachment", False):
                self._needs_plan_mode_exit_attachment = False
                plan_file_path = self._plan_file_path()
                exit_text = prompts.render(
                    "orchestrator/plan_mode_exit",
                    plan_file_path=str(plan_file_path),
                )
                if exit_text:
                    system_prompt.append(
                        PromptSection(
                            text=f"<system-reminder>\n{exit_text}\n</system-reminder>",
                            cache=False,
                        )
                    )
            return  # Not in plan mode — nothing else to inject.

        # --- In plan mode ---
        self._plan_mode_turn_count += 1

        # Gap 6: throttle — skip injection if not enough turns passed
        # (except first turn, which always gets the full prompt).
        if self._plan_mode_attachment_count > 0:
            if self._plan_mode_turn_count < self._TURNS_BETWEEN_ATTACHMENTS:
                return
        self._plan_mode_turn_count = 0  # reset after injection
        self._plan_mode_attachment_count += 1

        plan_file_path = self._plan_file_path()

        # --- Gap 8: one-shot reentry notification ---
        if getattr(self, "_has_exited_plan_mode", False):
            from kernel.plans import get_plan

            if get_plan(self._session_id) is not None:
                self._has_exited_plan_mode = False
                reentry_text = prompts.render(
                    "orchestrator/plan_mode_reentry",
                    plan_file_path=str(plan_file_path),
                )
                if reentry_text:
                    system_prompt.append(
                        PromptSection(
                            text=f"<system-reminder>\n{reentry_text}\n</system-reminder>",
                            cache=False,
                        )
                    )

        # Gap 5+6: full vs sparse plan mode reminder.
        is_full = (self._plan_mode_attachment_count % self._FULL_REMINDER_EVERY_N) == 1
        if is_full:
            from kernel.plans import get_plan

            existing = get_plan(self._session_id) is not None
            if existing:
                plan_file_info = (
                    f"A plan file already exists at {plan_file_path}. "
                    "You can read it and make incremental edits using the FileEdit tool."
                )
            else:
                plan_file_info = (
                    f"No plan file exists yet. You should create your plan at "
                    f"{plan_file_path} using the FileWrite tool."
                )
            text = prompts.render(
                "orchestrator/plan_mode",
                plan_file_info=plan_file_info,
            )
        else:
            text = prompts.render(
                "orchestrator/plan_mode_sparse",
                plan_file_path=str(plan_file_path),
            )

        if text:
            system_prompt.append(
                PromptSection(
                    text=f"<system-reminder>\n{text}\n</system-reminder>",
                    cache=False,
                )
            )

    def _plan_file_path(self) -> str:
        """Return the plan file path for this session."""
        from kernel.plans import get_plan_file_path

        return str(get_plan_file_path(self._session_id))

    # ------------------------------------------------------------------
    # Internal: main query loop
    # ------------------------------------------------------------------

    async def _run_query(
        self,
        prompt: list[Any],
        *,
        on_permission: PermissionCallback,
        token_budget: int | None = None,
        max_turns: int = 0,
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        """The main LLM ↔ tool loop.

        Mirrors Claude Code's ``queryLoop()`` 6-step structure:

        ┌─────────────────────────────────────────────────────────┐
        │  0. SETUP (before loop)                                 │
        │     Drain reminders, append user message to history     │
        │                                                         │
        │  while True:                                            │
        │    1. PREPARE — compress / trim context                 │
        │    2. BUILD PROMPT — rebuild system prompt each turn    │
        │    3. STREAM LLM — call model, distribute chunks       │
        │    4. COMMIT + BRANCH — save to history, check tools   │
        │    5. STOP (no tool_use) — hooks, error recovery       │
        │    6. TOOLS — execute, post-process, loop back          │
        └─────────────────────────────────────────────────────────┘
        """
        logger.info("Orchestrator[%s]: _run_query START", self._session_id)
        self._turn_input_tokens = 0
        self._turn_output_tokens = 0
        try:
            # ================================================================
            # STEP 0: SETUP (before loop)
            # ================================================================

            # 0a. Drain pending reminders from hooks
            reminders = _drain_pending_reminders(self._deps)

            # 0a.1 Drain pending messages from SendMessage (sub-agent only).
            # Messages queued by the parent agent via SendMessageTool are
            # injected as system-reminder blocks alongside hook reminders.
            if self._agent_id and self._deps.task_registry:
                agent_msgs = self._deps.task_registry.drain_messages(self._agent_id)
                for msg in agent_msgs:
                    reminders.append(f"Message from parent agent:\n{msg}")

            prompt_text = _extract_text(prompt)
            if reminders:
                prompt_text = _format_reminders(reminders, prompts=self._deps.prompts) + prompt_text

            # 0b. user_prompt_submit hook
            # CC fires this before appending to history (query.ts submitMessage).
            # The hook can block the query or rewrite the prompt text.
            blocked, hook_ctx = await self._fire_hook(
                event=HookEvent.USER_PROMPT_SUBMIT,
                user_text=prompt_text,
            )
            if blocked:
                self._stop_reason = StopReason.hook_blocked
                yield UserPromptBlocked(reason="user_prompt_submit hook blocked")
                return
            # Honour possible prompt rewrite by the hook handler.
            if hook_ctx.user_text is not None and hook_ctx.user_text != prompt_text:
                prompt_text = hook_ctx.user_text

            # 0c. Append user message to history
            self._history.append_user(
                _to_text_content(prompt, reminders=reminders, prompts=self._deps.prompts)
            )
            yield HistoryAppend(message=self._history.messages[-1])

            effective_max_turns = max_turns  # 0 = unlimited
            reactive_retries = 0
            turn = 0
            last_stop_reason: str | None = None
            max_tokens_override: int | None = None
            max_tokens_retries: int = 0

            # ================================================================
            # MAIN LOOP — LLM ↔ tool iterations
            # ================================================================
            while True:
                turn += 1
                last_stop_reason = None
                if effective_max_turns > 0 and turn > effective_max_turns:
                    logger.warning(
                        "Orchestrator[%s]: max_turns (%d) reached",
                        self._session_id,
                        effective_max_turns,
                    )
                    self._stop_reason = StopReason.max_turns
                    return

                # WONTFIX: tool-use summary await point (CC query.ts:1055-1060)
                # Paired with the WONTFIX at step 6c — no summary to await.

                # ============================================================
                # STEP 1: PREPARE — compress / trim context
                # ============================================================
                # 5 layers, cheap → expensive.  Each layer only runs if
                # previous layers didn't free enough space.
                #
                #   1a. tool-result budget  — enforced at ToolExecutor level
                #   1b. snip               — replace read-only results with placeholder
                #   1c. microcompact       — remove entire read-only tool pairs
                #   1d. context collapse   — TODO (feature-flagged, deferred)
                #   1e. autocompact        — LLM-driven summarization

                # 1a. Already applied in ToolExecutor._execute_one (step 6.5).

                # 1b. Snip — cheap O(1) pass, runs every iteration.
                self._compactor.snip(self._history)

                # 1c. Microcompact — only if still over threshold after snip.
                threshold = self._compaction_threshold()
                if self._history.token_count > threshold:
                    self._compactor.microcompact(self._history)

                # 1d. Context collapse — TODO (feature-flagged, deferred)

                # 1e. Autocompact (proactive) — last resort, calls the LLM.
                if self._history.token_count > threshold:
                    before = self._history.token_count
                    await self._compactor.compact(self._history)
                    after = self._history.token_count
                    logger.info(
                        "Orchestrator[%s]: compacted %d → %d tokens",
                        self._session_id,
                        before,
                        after,
                    )
                    yield CompactionEvent(tokens_before=before, tokens_after=after)
                    yield HistorySnapshot(messages=list(self._history.messages))

                # ============================================================
                # STEP 2: BUILD PROMPT — rebuild system prompt each turn
                # ============================================================
                # CC rebuilds every iteration (query.ts:449) because plan_mode
                # or model may change during tool execution.
                system_prompt = await self._prompt_builder.build(
                    prompt_text,
                    cwd=self._cwd,
                    model=self._config.model,
                    language=self._config.language,
                )

                # Debug: dump assembled system prompt on Turn 1.
                # Set MUSTANG_DUMP_SYSTEM_PROMPT=/path/to/file to enable.
                if turn == 1:
                    _dump_system_prompt(system_prompt, self._session_id, self._config.model)

                # ============================================================
                # STEP 3: STREAM LLM — call model, distribute chunks
                # ============================================================
                text_chunks: list[str] = []
                thought_chunks: list[ThoughtChunk] = []
                tool_calls: list[ToolUseContent] = []

                # 3a. Snapshot tool schemas for this turn
                logger.info(
                    "Orchestrator[%s]: turn %d, building tool schemas", self._session_id, turn
                )
                tool_schemas: list[ToolSchema] = []
                snapshot = None
                if self._deps.tool_source is not None:
                    try:
                        snapshot = self._deps.tool_source.snapshot_for_session(
                            session_id=self._session_id,
                            plan_mode=self.plan_mode,
                        )
                        tool_schemas = list(snapshot.schemas)

                        # 3a.1 Inject deferred tool listing so the LLM
                        # knows which tools it can load via ToolSearch.
                        if snapshot.deferred_listing:
                            system_prompt.append(
                                PromptSection(
                                    text=(
                                        "<system-reminder>\n"
                                        + snapshot.deferred_listing
                                        + "\n</system-reminder>"
                                    ),
                                    cache=False,
                                )
                            )
                    except Exception:
                        logger.exception(
                            "Orchestrator[%s]: tool_source.snapshot failed — running tool-less",
                            self._session_id,
                        )

                # 3a.2 Inject session-specific guidance (dynamic, tool-aware).
                #       CC: getSessionSpecificGuidanceSection(enabledTools, skillToolCommands)
                # Use schema + deferred names (LLM-visible tools), not lookup
                # keys.  Lookup also contains repl-hidden tools (e.g.
                # REPL_HIDDEN_TOOLS) which must not generate guidance bullets
                # the LLM can't act on.  Deferred tools are included because
                # the LLM knows about them via the deferred listing.
                snapshot_tool_names = (
                    {s.name for s in snapshot.schemas} | snapshot.deferred_names
                    if snapshot else set()
                )
                self._inject_session_guidance(system_prompt, snapshot_tool_names)

                # 3a.3 Inject plan mode / exit / reentry instructions.
                self._inject_plan_mode_prompts(system_prompt)

                # 3b. Stream LLM response
                #
                # A per-turn ToolExecutor is created before the stream loop.
                # When streaming_tools=True, tool_use blocks are fed to the
                # executor immediately via add_tool() so safe tools start
                # running while the LLM is still streaming.  When False
                # (default), tool_uses are collected and fed after the stream.
                # Use deps.set_mode (Session-layer closure) when available;
                # fall back to Orchestrator-level methods for degraded mode.
                set_mode_closure = self._deps.set_mode or self.set_mode

                def _handle_ctx_change(new_ctx: Any) -> None:
                    """Callback when a tool's context_modifier fires."""
                    self._cwd = new_ctx.cwd
                    executor._cwd = new_ctx.cwd  # update per-turn executor too
                    git = getattr(self._deps, "git", None)
                    if git is not None:
                        git.invalidate_context(self._session_id)

                executor = ToolExecutor(
                    deps=self._deps,
                    session_id=self._session_id,
                    cwd=self._cwd,
                    agent_depth=self._depth,
                    agent_id=self._agent_id,
                    spawn_subagent=self._make_spawn_subagent(),
                    set_plan_mode=set_mode_closure,
                    set_mode=set_mode_closure,
                    on_context_changed=_handle_ctx_change,
                    streaming=self._config.streaming_tools,
                )
                streaming_tools = self._config.streaming_tools

                logger.info(
                    "Orchestrator[%s]: calling provider.stream with %d tools (streaming_tools=%s)",
                    self._session_id,
                    len(tool_schemas),
                    streaming_tools,
                )
                try:
                    async for chunk in await self._deps.provider.stream(
                        system=system_prompt,
                        messages=self._history.messages,
                        tool_schemas=tool_schemas,
                        model=self._config.model,
                        temperature=self._config.temperature,
                        max_tokens=max_tokens_override,
                    ):
                        match chunk:
                            case TextChunk(content=text):
                                text_chunks.append(text)
                                yield TextDelta(content=text)

                            case ThoughtChunk() as tc:
                                thought_chunks.append(tc)
                                if tc.content:
                                    yield ThoughtDelta(content=tc.content)

                            case ToolUseChunk() as tc:
                                tu = ToolUseContent(id=tc.id, name=tc.name, input=tc.input)
                                tool_calls.append(tu)
                                if streaming_tools:
                                    executor.add_tool(tu)

                            case UsageChunk() as u:
                                self._history.update_token_count(u.input_tokens, u.output_tokens)
                                self._turn_input_tokens += u.input_tokens
                                self._turn_output_tokens += u.output_tokens
                                last_stop_reason = u.stop_reason

                            case StreamError() as e:
                                logger.warning(
                                    "Orchestrator[%s]: stream error: %s",
                                    self._session_id,
                                    e.message,
                                )
                                executor.discard()
                                yield QueryError(message=e.message, code=e.code)
                                self._stop_reason = StopReason.error
                                return

                except PromptTooLongError as exc:
                    # Reactive compaction — retry after compressing history.
                    if reactive_retries >= _MAX_REACTIVE_RETRIES:
                        logger.error(
                            "Orchestrator[%s]: prompt too long after %d retries",
                            self._session_id,
                            reactive_retries,
                        )
                        yield QueryError(message=str(exc), code="prompt_too_long")
                        self._stop_reason = StopReason.error
                        return
                    reactive_retries += 1
                    logger.warning(
                        "Orchestrator[%s]: reactive compact (attempt %d/%d)",
                        self._session_id,
                        reactive_retries,
                        _MAX_REACTIVE_RETRIES,
                    )
                    before = self._history.token_count
                    await self._compactor.compact(self._history)
                    yield CompactionEvent(
                        tokens_before=before, tokens_after=self._history.token_count
                    )
                    turn -= 1
                    continue

                except MediaSizeError as exc:
                    # Strip images from history and retry.
                    if reactive_retries >= _MAX_REACTIVE_RETRIES:
                        logger.error(
                            "Orchestrator[%s]: media size error after %d retries",
                            self._session_id,
                            reactive_retries,
                        )
                        yield QueryError(message=str(exc), code="media_size")
                        self._stop_reason = StopReason.error
                        return
                    reactive_retries += 1
                    logger.warning(
                        "Orchestrator[%s]: media_size — stripping images + compact (attempt %d/%d)",
                        self._session_id,
                        reactive_retries,
                        _MAX_REACTIVE_RETRIES,
                    )
                    stripped = self._compactor.strip_media(self._history)
                    if stripped > 0:
                        before = self._history.token_count
                        await self._compactor.compact(self._history)
                        yield CompactionEvent(
                            tokens_before=before,
                            tokens_after=self._history.token_count,
                        )
                    turn -= 1
                    continue

                except ProviderError as exc:
                    logger.error("Orchestrator[%s]: provider error: %s", self._session_id, exc)
                    yield QueryError(message=str(exc))
                    self._stop_reason = StopReason.error
                    return

                # 3c. post_sampling hook (query.ts:999-1009)
                # Fires after every LLM stream completes, before abort
                # check and tool/stop branching.  Non-blocking, pure
                # notification — can_block=False in EVENT_SPECS.
                if text_chunks or tool_calls:
                    await self._fire_hook(event=HookEvent.POST_SAMPLING)

                # 3d. Abort check ① (query.ts:1015-1052)
                # Give any pending CancelledError a chance to surface
                # *before* we commit the assistant turn to history.
                # Without this, cancel between stream-end and tool-start
                # would leave orphan tool_use blocks in history.
                await asyncio.sleep(0)

                # ============================================================
                # STEP 4: COMMIT + BRANCH — save to history, check for tools
                # ============================================================
                _n = len(self._history.messages)
                self._history.append_assistant(
                    text="".join(text_chunks),
                    thoughts=list(thought_chunks),
                    tool_calls=tool_calls,
                )
                if len(self._history.messages) > _n:
                    yield HistoryAppend(message=self._history.messages[-1])

                if not tool_calls:
                    # ========================================================
                    # STEP 5: STOP (no tool_use)
                    # ========================================================

                    # 5a. max_output_tokens recovery (withhold pattern)
                    # CC: query.ts:1223 — escalate 8k → 64k, retry ×3
                    if (
                        last_stop_reason == "max_tokens"
                        and max_tokens_retries < _MAX_OUTPUT_TOKEN_RETRIES
                    ):
                        max_tokens_retries += 1
                        max_tokens_override = _MAX_TOKENS_ESCALATED
                        logger.warning(
                            "Orchestrator[%s]: max_output_tokens hit — "
                            "escalating to %d (retry %d/%d)",
                            self._session_id,
                            _MAX_TOKENS_ESCALATED,
                            max_tokens_retries,
                            _MAX_OUTPUT_TOKEN_RETRIES,
                        )
                        # Withhold: undo the partial assistant turn committed
                        # in STEP 4 so the LLM retries from the same state.
                        self._history.pop_last_assistant()
                        continue

                    # Reset escalation state on clean stop.
                    max_tokens_override = None
                    max_tokens_retries = 0

                    # 5b. Stop hook (notification-only, can_block=False)
                    # CC: handleStopHooks() — query.ts:1267
                    await self._fire_hook(
                        event=HookEvent.STOP,
                        stop_reason=last_stop_reason or "end_turn",
                        message_count=len(self._history.messages),
                        token_estimate=self._history.token_count,
                    )

                    # 5c. Token budget check — CC: query.ts:1308-1355
                    if token_budget is not None:
                        used = self._turn_input_tokens + self._turn_output_tokens
                        if used >= token_budget:
                            logger.warning(
                                "Orchestrator[%s]: token budget exceeded (%d >= %d)",
                                self._session_id,
                                used,
                                token_budget,
                            )
                            yield QueryError(
                                message=(
                                    f"Token budget exceeded: {used} tokens "
                                    f"used, budget was {token_budget}"
                                ),
                                code="token_budget_exceeded",
                            )
                            self._stop_reason = StopReason.budget_exceeded
                            return

                    self._stop_reason = StopReason.end_turn
                    return

                # ============================================================
                # STEP 6: TOOLS — execute, post-process, loop back
                # ============================================================

                # 6a. Feed remaining tools and finalize.
                if not streaming_tools:
                    for tu in tool_calls:
                        executor.add_tool(tu)
                executor.finalize_stream()

                # 6b. Consume results (parallel within safe batches).
                tool_results: list[ToolResultContent] = []
                tool_mode: Literal["default", "plan", "bypass"] = (
                    "plan" if self._mode == "plan"
                    else "bypass" if self._mode == "bypass"
                    else "default"
                )
                async for event, result in executor.results(
                    on_permission=on_permission,
                    mode=tool_mode,
                ):
                    # Record tool kind for compression layers (1b snip / 1c microcompact).
                    if isinstance(event, ToolCallStart):
                        self._history.record_tool_kind(event.id, event.kind)
                    yield event
                    if result is not None:
                        tool_results.append(result)

                # 6b'. Abort check ② (query.ts:1485-1516)
                # Yield control so a pending CancelledError (from Session.cancel)
                # can surface before we commit tool results to history.
                await asyncio.sleep(0)

                # WONTFIX: 6c. Haiku tool-use summary (CC query.ts:1411-1482)
                # CC fires an async Haiku call here to generate a ≤30-char
                # summary label for the SDK/mobile progress UI, awaited at
                # the start of the next iteration.  We skip this because:
                #   1. Mustang clients already get ToolCallStart.title +
                #      ToolCallResult — sufficient for UI rendering.
                #   2. An extra API call per tool batch for a cosmetic
                #      label is poor cost/complexity trade-off.
                #   3. Neither OpenClaw nor Hermes implement this.
                # Infra is ready if needed: add a "summary" role to
                # CurrentUsedConfig + reuse Compactor._summarise() pattern.

                # 6d. Drain task notifications + GC
                if self._deps.task_registry is not None:
                    completed_ids = self._deps.task_registry.drain_notifications(
                        agent_id=self._agent_id,
                    )
                    for task_id in completed_ids:
                        task = self._deps.task_registry.get(task_id)
                        if task is not None:
                            notification = _format_task_notification(task)
                            if self._deps.queue_reminders is not None:
                                self._deps.queue_reminders([notification])
                    self._deps.task_registry.evict_terminal()

                # 6d'. Drain monitor line buffers
                if self._deps.task_registry is not None:
                    monitor_lines = self._deps.task_registry.drain_monitor_lines(
                        agent_id=self._agent_id,
                    )
                    for task_id, lines in monitor_lines.items():
                        if lines and self._deps.queue_reminders is not None:
                            notification = _format_monitor_notification(task_id, lines)
                            self._deps.queue_reminders([notification])

                # 6e. Append tool results to history and continue loop
                _n = len(self._history.messages)
                self._history.append_tool_results(tool_results)
                if len(self._history.messages) > _n:
                    yield HistoryAppend(message=self._history.messages[-1])

        except asyncio.CancelledError:
            logger.debug("Orchestrator[%s]: cancelled", self._session_id)
            self._stop_reason = StopReason.cancelled

            # Patch orphan tool_use blocks — the Anthropic API requires
            # every tool_use to have a matching tool_result.  If cancel
            # arrived after append_assistant() but before tool execution
            # completed, we synthesise error results so history stays
            # well-formed.  (CC: query.ts:1015-1052)
            orphan_ids = self._history.pending_tool_use_ids()
            if orphan_ids:
                synthetic = [
                    ToolResultContent(
                        tool_use_id=tid,
                        content="Interrupted by user",
                        is_error=True,
                    )
                    for tid in orphan_ids
                ]
                self._history.append_tool_results(synthetic)
                yield HistoryAppend(message=self._history.messages[-1])

            yield CancelledEvent()
            return

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compaction_threshold(self) -> int:
        if not hasattr(self, "_cached_context_window"):
            self._cached_context_window = _DEFAULT_CONTEXT_WINDOW
        return int(self._cached_context_window * _COMPACTION_FRACTION)

    async def _fire_hook(
        self,
        *,
        event: HookEvent,
        user_text: str | None = None,
        message_count: int | None = None,
        token_estimate: int | None = None,
        stop_reason: str | None = None,
    ) -> tuple[bool, HookEventCtx]:
        """Fire ``event`` through ``deps.hooks`` and drain reminders.

        Returns ``(blocked, ctx)``.  When ``deps.hooks`` is ``None``
        (HookManager unavailable) this is a no-op that always returns
        ``(False, <empty ctx>)`` — callers still get a ctx for uniform
        code paths (e.g. reading ``ctx.user_text`` after a no-op fire
        just returns the original value).
        """
        ambient = AmbientContext(
            session_id=self._session_id,
            cwd=self._cwd,
            agent_depth=self._depth,
            mode=self._mode,
            timestamp=time.time(),
        )
        ctx = HookEventCtx(
            event=event,
            ambient=ambient,
            user_text=user_text,
            message_count=message_count,
            token_estimate=token_estimate,
            stop_reason=stop_reason,
        )
        hooks = self._deps.hooks
        if hooks is None:
            return False, ctx

        blocked = await hooks.fire(ctx)

        # Drain any system_reminder messages handlers appended onto
        # the Session's pending_reminders list.
        drain = self._deps.queue_reminders
        if drain is not None and ctx.messages:
            drain(list(ctx.messages))
        return blocked, ctx

    def _make_spawn_subagent(self) -> Any:
        """Build a ``spawn_subagent`` closure for ``ToolContext``.

        Returns an async generator factory that creates a child
        ``StandardOrchestrator`` at depth+1 and transparently yields
        its events wrapped by ``SubAgentStart`` / ``SubAgentEnd``.
        """
        from kernel.orchestrator.events import SubAgentEnd, SubAgentStart
        from kernel.orchestrator.types import PermissionResponse
        from kernel.tasks.id import generate_task_id
        from kernel.tasks.types import TaskType

        parent = self

        async def _auto_allow(_req: Any) -> PermissionResponse:
            """Sub-agents auto-allow all tool calls."""
            return PermissionResponse(decision="allow_once")

        async def spawn_subagent(
            prompt: str,
            attachments: list[Any],
            *,
            agent_id: str | None = None,
            on_permission: PermissionCallback | None = None,
            initial_history: list[Any] | None = None,
        ) -> AsyncGenerator[Any, None]:
            if agent_id is None:
                agent_id = generate_task_id(TaskType.local_agent)

            child = StandardOrchestrator(
                deps=parent._deps,
                session_id=f"{parent._session_id}/agent-{agent_id}",
                initial_history=initial_history or [],
                config=parent._config,
                depth=parent._depth + 1,
                cwd=parent._cwd,
                agent_id=agent_id,
            )

            yield SubAgentStart(
                agent_id=agent_id,
                description=prompt[:80],
                agent_type="general-purpose",
                spawned_by_tool_id="",
            )

            # Convert string prompt to content blocks for query()
            prompt_blocks = [TextContent(text=prompt)]
            perm_cb = on_permission or _auto_allow

            async for event in child.query(
                prompt_blocks,
                on_permission=perm_cb,
                max_turns=_SUBAGENT_DEFAULT_MAX_TURNS,
            ):
                yield event

            # Capture transcript for potential resume via SendMessage.
            transcript = list(child._history.messages) if child._history.messages else None

            yield SubAgentEnd(
                agent_id=agent_id,
                stop_reason=child.stop_reason or StopReason.end_turn,
                transcript=transcript,
            )

            # Orphan drain: after sub-agent ends, take over its
            # undelivered task notifications.
            parent._drain_orphan_notifications(agent_id)

        return spawn_subagent

    def _drain_orphan_notifications(self, ended_agent_id: str) -> None:
        """After a sub-agent ends, claim its remaining task notifications."""
        registry = self._deps.task_registry
        if registry is None:
            return
        orphans = registry.drain_notifications(agent_id=ended_agent_id)
        for task_id in orphans:
            task = registry.get(task_id)
            if task is not None:
                notification = _format_task_notification(task)
                if self._deps.queue_reminders is not None:
                    self._deps.queue_reminders([notification])


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _extract_text(blocks: list[Any]) -> str:
    """Concatenate visible text from a list of content blocks."""
    parts: list[str] = []
    for b in blocks:
        if isinstance(b, str):
            parts.append(b)
        else:
            text = getattr(b, "text", None)
            if isinstance(text, str):
                parts.append(text)
    return " ".join(parts)


def _to_text_content(
    blocks: list[Any],
    *,
    reminders: list[str] | None = None,
    prompts: object = None,
) -> list[TextContent]:
    """Normalise content blocks to ``list[TextContent]``.

    When ``reminders`` is non-empty, a single leading ``TextContent``
    with the concatenated ``<system-reminder>`` blocks is prepended so
    the persisted history matches what the LLM saw.
    """
    result: list[TextContent] = []
    if reminders:
        result.append(TextContent(text=_format_reminders(reminders, prompts=prompts)))
    for b in blocks:
        if isinstance(b, TextContent):
            result.append(b)
        elif isinstance(b, str):
            result.append(TextContent(text=b))
        else:
            text = getattr(b, "text", None)
            if isinstance(text, str):
                result.append(TextContent(text=text))
    return result or [TextContent(text="")]


def _drain_pending_reminders(deps: OrchestratorDeps) -> list[str]:
    """Pop and return any hook-queued system_reminder strings.

    ``deps.drain_reminders`` is a SessionManager-provided closure; it
    mutates the Session's ``pending_reminders`` list so subsequent turns
    start with an empty buffer.  ``None`` means no SessionManager (tests
    or degraded mode) and we short-circuit to an empty list.
    """
    drain = deps.drain_reminders
    if drain is None:
        return []
    try:
        return list(drain())
    except Exception:
        logger.exception("drain_reminders raised — treating as empty")
        return []


def _format_reminders(reminders: list[str], prompts: object = None) -> str:
    """Wrap each reminder in an ``<system-reminder>`` block.

    Claude Code's pattern — keeps reminders visually distinct in the
    transcript and easy to parse if the LLM needs to reason about them.
    Joined with blank lines so the next real prompt reads cleanly.

    When ``prompts`` (a PromptManager) is provided, the wrapper
    template is loaded from ``orchestrator/system_reminder``.
    """
    if prompts is not None:
        tpl = prompts.get("orchestrator/system_reminder")  # type: ignore[attr-defined,union-attr]
        blocks = [tpl.format(reminder=r) for r in reminders]
    else:
        blocks = [f"<system-reminder>\n{r}\n</system-reminder>" for r in reminders]
    return "\n\n".join(blocks) + "\n\n"


# ---------------------------------------------------------------------------
# Task notification formatting
# ---------------------------------------------------------------------------


def _format_task_notification(task: object) -> str:
    """Format a completed task as a ``<task-notification>`` XML block.

    Aligned with Claude Code ``enqueueShellNotification`` /
    ``enqueueAgentNotification`` format.
    """
    from kernel.tasks.types import AgentTaskState, MonitorTaskState, ShellTaskState

    status = task.status.value  # type: ignore[attr-defined,union-attr]
    description = task.description  # type: ignore[attr-defined,union-attr]
    task_id = task.id  # type: ignore[attr-defined,union-attr]

    if isinstance(task, MonitorTaskState):
        if status == "completed":
            summary = f'Monitor "{description}" stopped'
            if task.exit_code is not None:
                summary += f" (exit code {task.exit_code})"
        elif status == "failed":
            summary = f'Monitor "{description}" failed'
            if task.exit_code is not None:
                summary += f" with exit code {task.exit_code}"
        else:
            summary = f'Monitor "{description}" was stopped'
    elif isinstance(task, ShellTaskState):
        if status == "completed":
            summary = f'Background command "{description}" completed'
            if task.exit_code is not None:
                summary += f" (exit code {task.exit_code})"
        elif status == "failed":
            summary = f'Background command "{description}" failed'
            if task.exit_code is not None:
                summary += f" with exit code {task.exit_code}"
        else:
            summary = f'Background command "{description}" was stopped'
    elif isinstance(task, AgentTaskState):
        if status == "completed":
            summary = f'Agent "{description}" completed'
        elif status == "failed":
            summary = f'Agent "{description}" failed: {task.error or "Unknown error"}'
        else:
            summary = f'Agent "{description}" was stopped'
    else:
        summary = f'Task "{description}" {status}'

    tool_use_line = ""
    tool_use_id = getattr(task, "tool_use_id", None)
    if tool_use_id:
        tool_use_line = f"\n<tool-use-id>{tool_use_id}</tool-use-id>"

    result_section = ""
    if isinstance(task, AgentTaskState) and task.result:
        result_section = f"\n<result>{task.result}</result>"

    output_file = getattr(task, "output_file", "")
    return (
        f"<task-notification>\n"
        f"<task-id>{task_id}</task-id>{tool_use_line}\n"
        f"<output-file>{output_file}</output-file>\n"
        f"<status>{status}</status>\n"
        f"<summary>{summary}</summary>{result_section}\n"
        f"</task-notification>"
    )


def _format_monitor_notification(task_id: str, lines: list[str]) -> str:
    """Format buffered monitor lines as a ``<monitor-update>`` XML block.

    Injected by the Orchestrator at step 6d' each turn.
    """
    body = "\n".join(lines)
    return (
        f"<monitor-update>\n"
        f"<task-id>{task_id}</task-id>\n"
        f"<output>\n{body}\n</output>\n"
        f"</monitor-update>"
    )
