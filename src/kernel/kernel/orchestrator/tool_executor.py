"""ToolExecutor — streaming-shaped tool dispatch with parallel execution.

Drives each ``ToolUseContent`` from the LLM through the seven-step
pipeline documented in ``docs/plans/landed/tool-manager.md`` § 6:

    1. validate_input
    2. authorize (with on_permission round-trip if ask)
    3. pre_tool_use hook (only when authorize allowed)
    4. ToolCallStart event
    5. tool.call() — stream progress + final result
    6. post_tool_use hook
    7. emit ToolCallResult / ToolCallError + (future) ToolCallDisplay

Streaming-shaped interface (tool-manager.md § 6.3)
---------------------------------------------------
The executor exposes ``add_tool()`` / ``finalize_stream()`` /
``results()`` / ``discard()`` regardless of streaming mode.

- ``streaming=False`` (default): tools are queued by ``add_tool()``,
  partitioned and dispatched after ``finalize_stream()``.
- ``streaming=True``: safe tools start immediately on ``add_tool()``;
  non-safe tools queue until ``finalize_stream()``.

Both paths share the same ``results()`` consumption loop in the
Orchestrator.

Concurrency model
-----------------
Consecutive concurrency-safe tools form a parallel batch; non-safe
tools execute alone.  Within a batch, tools run as ``asyncio.Task``
instances capped by a semaphore.  Permission prompts are serialized
via an ``asyncio.Lock`` so the UI never shows two prompts at once.

Degradation:
- ``deps.tool_source`` None → no Tool can be resolved → each call
  yields ``ToolCallError("tool registry not available")``.
- ``deps.authorizer`` None → allow-all fallback (log a warning once
  per executor instance so the degraded state is visible but not
  spammy).
"""

from __future__ import annotations

import asyncio
import orjson
import logging
import time
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from kernel.hooks import AmbientContext, HookEvent, HookEventCtx
from kernel.llm.types import TextContent, ToolResultContent, ToolUseContent
from kernel.orchestrator.events import (
    OrchestratorEvent,
    ToolCallError,
    ToolCallResult as ToolCallResultEvent,
    ToolCallStart,
)
from kernel.orchestrator.events import ToolCallProgress as ToolCallProgressEvent
from kernel.orchestrator.types import (
    OrchestratorDeps,
    PermissionCallback,
    PermissionRequest,
    PermissionRequestOption,
    PermissionResponse,
    ToolKind,
)

if TYPE_CHECKING:
    from kernel.tool_authz import AuthorizeContext, PermissionDecision, ToolAuthorizer
    from kernel.tools import Tool, ToolManager
    from kernel.tools.context import ToolContext

logger = logging.getLogger(__name__)

# Tools that mutate files — trigger SkillManager dynamic discovery.
_FILE_MUTATING_TOOLS = frozenset({"FileEdit", "FileWrite"})

# Sentinel placed in per-tool queues to signal "no more events".
_SENTINEL = None

# Default max concurrent safe tools.
_DEFAULT_MAX_CONCURRENCY = 10

# Type alias for the (event, result) tuples yielded by results().
_EventPair = tuple[OrchestratorEvent, ToolResultContent | None]


# ---------------------------------------------------------------------------
# Partitioning
# ---------------------------------------------------------------------------


def partition_tool_calls(
    tool_calls: list[ToolUseContent],
    lookup: Any,  # ToolManager.lookup — (str) -> Tool | None
) -> list[list[ToolUseContent]]:
    """Split tool_calls into ordered batches for execution.

    Consecutive concurrency-safe tools form a single batch that will be
    executed in parallel.  Non-safe tools are always singleton batches
    executed serially.

    Example::

        [safe, safe, unsafe, safe, unsafe]
        → [[safe, safe], [unsafe], [safe], [unsafe]]

    Unknown tool names are treated as non-safe (singleton batch).
    """
    if not tool_calls:
        return []

    batches: list[list[ToolUseContent]] = []
    safe_acc: list[ToolUseContent] = []

    for tc in tool_calls:
        tool = lookup(tc.name) if lookup is not None else None
        is_safe = tool is not None and tool.is_concurrency_safe

        if is_safe:
            safe_acc.append(tc)
        else:
            # Flush accumulated safe tools as one batch.
            if safe_acc:
                batches.append(safe_acc)
                safe_acc = []
            # Non-safe tool is a singleton batch.
            batches.append([tc])

    # Flush trailing safe accumulator.
    if safe_acc:
        batches.append(safe_acc)

    return batches


class ToolExecutor:
    """Streaming-shaped executor with parallel batch support.

    One instance per tool-execution phase within a query turn.
    """

    def __init__(
        self,
        deps: OrchestratorDeps,
        *,
        session_id: str,
        cwd: Path,
        agent_depth: int = 0,
        agent_id: str | None = None,
        spawn_subagent: Any = None,
        set_plan_mode: Any = None,
        set_mode: Any = None,
        on_context_changed: Any = None,
        streaming: bool = False,
        max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
    ) -> None:
        self._deps = deps
        self._session_id = session_id
        self._cwd = cwd
        self._agent_depth = agent_depth
        self._agent_id = agent_id
        self._spawn_subagent = spawn_subagent
        self._set_plan_mode = set_plan_mode
        self._set_mode = set_mode
        self._on_context_changed = on_context_changed
        self._streaming = streaming
        self._max_concurrency = max_concurrency
        self._allow_all_warned = False

        # Queued tool_use blocks waiting for dispatch.
        self._queue: list[ToolUseContent] = []
        self._finalized = False
        self._discarded = False

        # Permission prompts must be serialized — only one UI prompt at
        # a time, even when multiple safe tools run concurrently.
        self._permission_lock = asyncio.Lock()

        # Track in-flight tool contexts and tasks for discard().
        self._active_contexts: dict[str, asyncio.Event] = {}
        self._active_tasks: dict[str, asyncio.Task[None]] = {}

        # Concurrency limiter for safe batches.
        self._semaphore = asyncio.Semaphore(max_concurrency)

    # ------------------------------------------------------------------
    # Public streaming-shaped interface
    # ------------------------------------------------------------------

    def add_tool(self, tool_use: ToolUseContent) -> None:
        """Accept a tool_use block.

        - ``streaming=False``: queues for later dispatch after
          ``finalize_stream()``.
        - ``streaming=True``: safe tools may start immediately if no
          non-safe tool is currently executing.  Non-safe tools queue
          until ``finalize_stream()``.
        """
        if self._discarded:
            return
        self._queue.append(tool_use)
        # Streaming-mode eager dispatch is handled in results() —
        # for Phase 2 we can add immediate dispatch here.

    def finalize_stream(self) -> None:
        """Signal that no more ``add_tool()`` calls will arrive.

        For ``streaming=False`` this is the trigger to partition and
        begin execution (consumed by ``results()``).
        """
        self._finalized = True

    def discard(self) -> None:
        """Cancel all in-flight tools and clear the queue.

        Aligns with Claude Code's ``StreamingToolExecutor.discard()``.
        """
        self._discarded = True
        self._queue.clear()

        # Signal cancellation on all active tool contexts.
        for cancel_event in self._active_contexts.values():
            cancel_event.set()

        # Cancel asyncio tasks.
        for task in self._active_tasks.values():
            task.cancel()

        self._active_contexts.clear()
        self._active_tasks.clear()

    async def results(
        self,
        on_permission: PermissionCallback,
        mode: Literal["default", "plan", "bypass"] = "default",
    ) -> AsyncGenerator[_EventPair, None]:
        """Yield ``(event, ToolResultContent | None)`` for all queued tools.

        Must be called after ``finalize_stream()``.  Events from each
        tool maintain causal ordering (Start → Progress → Result).
        Within a concurrent batch, cross-tool ordering is by completion.
        """
        if not self._finalized:
            raise RuntimeError("results() called before finalize_stream()")

        tool_source = self._deps.tool_source
        lookup = tool_source.lookup if tool_source is not None else None
        batches = partition_tool_calls(self._queue, lookup)

        for batch in batches:
            if self._discarded:
                return

            if len(batch) == 1:
                # Single-tool batch — run directly (serial, no task overhead).
                async for pair in self._execute_single(batch[0], on_permission, mode):
                    yield pair
            else:
                # Multi-tool safe batch — run concurrently.
                async for pair in self._execute_batch_concurrent(batch, on_permission, mode):
                    yield pair

    # ------------------------------------------------------------------
    # Backward-compatible bridge
    # ------------------------------------------------------------------

    async def run(
        self,
        tool_calls: list[ToolUseContent],
        on_permission: PermissionCallback,
        plan_mode: bool = False,
        mode: Literal["default", "plan", "bypass"] = "default",
    ) -> AsyncGenerator[_EventPair, None]:
        """Legacy interface — wraps add_tool + finalize + results.

        Existing Orchestrator code can call this unchanged.
        """
        for tc in tool_calls:
            self.add_tool(tc)
        self.finalize_stream()
        effective_mode: Literal["default", "plan", "bypass"] = "plan" if plan_mode else mode
        return self.results(on_permission, effective_mode)

    # ------------------------------------------------------------------
    # Single-tool execution (no concurrency overhead)
    # ------------------------------------------------------------------

    async def _execute_single(
        self,
        tc: ToolUseContent,
        on_permission: PermissionCallback,
        mode: Literal["default", "plan", "bypass"],
    ) -> AsyncGenerator[_EventPair, None]:
        """Execute one tool call — the non-concurrent fast path.

        Even though this is a single tool, we track its cancel_event so
        ``discard()`` can signal cancellation, and run it in a task so
        the discard path can cancel the task if the tool doesn't honour
        the cancel_event.
        """
        tool_source = self._deps.tool_source
        authorizer = self._deps.authorizer

        tool = tool_source.lookup(tc.name) if tool_source is not None else None
        if tool is None:
            async for item in self._error_unknown_tool(tc):
                yield item
            return

        tool_ctx = self._build_tool_context(tool_source)
        auth_ctx = self._build_authorize_context(mode=mode)

        # Track for discard().
        self._active_contexts[tc.id] = tool_ctx.cancel_event

        q: asyncio.Queue[_EventPair | None] = asyncio.Queue()
        task = asyncio.create_task(
            self._run_one_to_queue(
                tc=tc,
                tool=tool,
                tool_ctx=tool_ctx,
                auth_ctx=auth_ctx,
                authorizer=authorizer,
                on_permission=on_permission,
                mode=mode,
                queue=q,
            ),
            name=f"tool-{tc.id}",
        )
        self._active_tasks[tc.id] = task

        try:
            while True:
                queued: _EventPair | None = await q.get()
                if queued is _SENTINEL:
                    break
                yield queued
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self._active_contexts.pop(tc.id, None)
            self._active_tasks.pop(tc.id, None)

    # ------------------------------------------------------------------
    # Concurrent batch execution
    # ------------------------------------------------------------------

    async def _execute_batch_concurrent(
        self,
        batch: list[ToolUseContent],
        on_permission: PermissionCallback,
        mode: Literal["default", "plan", "bypass"],
    ) -> AsyncGenerator[_EventPair, None]:
        """Execute a batch of concurrency-safe tools in parallel.

        Each tool writes events to its own ``asyncio.Queue``.  A merge
        loop pulls from all queues and yields in completion order.
        """
        tool_source = self._deps.tool_source
        authorizer = self._deps.authorizer

        queues: list[asyncio.Queue[_EventPair | None]] = []
        tasks: list[asyncio.Task[None]] = []

        for tc in batch:
            q: asyncio.Queue[_EventPair | None] = asyncio.Queue()
            queues.append(q)

            tool = tool_source.lookup(tc.name) if tool_source is not None else None
            if tool is None:
                # Queue the error events and close immediately.
                task = asyncio.create_task(
                    self._error_unknown_to_queue(tc, q),
                    name=f"tool-{tc.id}-unknown",
                )
            else:
                tool_ctx = self._build_tool_context(tool_source)
                auth_ctx = self._build_authorize_context(mode=mode)

                # Track the cancel event for discard().
                self._active_contexts[tc.id] = tool_ctx.cancel_event

                task = asyncio.create_task(
                    self._run_one_to_queue(
                        tc=tc,
                        tool=tool,
                        tool_ctx=tool_ctx,
                        auth_ctx=auth_ctx,
                        authorizer=authorizer,
                        on_permission=on_permission,
                        mode=mode,
                        queue=q,
                    ),
                    name=f"tool-{tc.id}",
                )

            self._active_tasks[tc.id] = task
            tasks.append(task)

        # Merge events from all queues in completion order.
        try:
            async for pair in self._merge_queues(queues):
                yield pair
        finally:
            # Ensure all tasks complete (even on generator close).
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

            # Cleanup tracking dicts.
            for tc in batch:
                self._active_contexts.pop(tc.id, None)
                self._active_tasks.pop(tc.id, None)

    async def _run_one_to_queue(
        self,
        *,
        tc: ToolUseContent,
        tool: Tool,
        tool_ctx: ToolContext,
        auth_ctx: AuthorizeContext,
        authorizer: ToolAuthorizer | None,
        on_permission: PermissionCallback,
        mode: Literal["default", "plan", "bypass"],
        queue: asyncio.Queue[_EventPair | None],
    ) -> None:
        """Run the 7-step pipeline for one tool, writing events to *queue*.

        Acquires the concurrency semaphore before starting.  Puts a
        ``None`` sentinel when finished (success or error).
        """
        try:
            async with self._semaphore:
                async for pair in self._run_one(
                    tc=tc,
                    tool=tool,
                    tool_ctx=tool_ctx,
                    auth_ctx=auth_ctx,
                    authorizer=authorizer,
                    on_permission=on_permission,
                    mode=mode,
                ):
                    await queue.put(pair)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("tool %s failed in concurrent batch", tc.name)
        finally:
            await queue.put(_SENTINEL)

    async def _error_unknown_to_queue(
        self,
        tc: ToolUseContent,
        queue: asyncio.Queue[_EventPair | None],
    ) -> None:
        """Write unknown-tool error events to *queue*, then sentinel."""
        try:
            async for item in self._error_unknown_tool(tc):
                await queue.put(item)
        finally:
            await queue.put(_SENTINEL)

    async def _merge_queues(
        self,
        queues: list[asyncio.Queue[_EventPair | None]],
    ) -> AsyncGenerator[_EventPair, None]:
        """Yield events from multiple queues in completion order.

        Each queue produces a stream of ``(event, result)`` tuples
        followed by a ``None`` sentinel.  This generator yields until
        all queues have sent their sentinel.
        """
        # Create one reader task per queue.
        pending: dict[int, asyncio.Task[_EventPair | None]] = {}
        for i, q in enumerate(queues):
            pending[i] = asyncio.create_task(q.get(), name=f"merge-{i}")

        active_indices = set(range(len(queues)))

        while active_indices:
            # Wait for any reader to produce an item.
            done, _ = await asyncio.wait(
                [pending[i] for i in active_indices],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                # Find which index this task belongs to.
                idx = next(i for i in active_indices if pending[i] is task)
                item = task.result()

                if item is _SENTINEL:
                    # This queue is exhausted.
                    active_indices.discard(idx)
                else:
                    yield item
                    # Schedule the next read from this queue.
                    pending[idx] = asyncio.create_task(queues[idx].get(), name=f"merge-{idx}")

    # ------------------------------------------------------------------
    # Per-call pipeline (the 7-step sequence)
    # ------------------------------------------------------------------

    async def _run_one(
        self,
        *,
        tc: ToolUseContent,
        tool: Tool,
        tool_ctx: ToolContext,
        auth_ctx: AuthorizeContext,
        authorizer: ToolAuthorizer | None,
        on_permission: PermissionCallback,
        mode: Literal["default", "plan", "bypass"],
    ) -> AsyncGenerator[_EventPair, None]:
        # (1) validate_input — cheap, rejects malformed inputs before hooks/auth.
        try:
            await tool.validate_input(tc.input, tool_ctx)
        except Exception as exc:
            yield self._error_tuple(tc, f"invalid input: {exc}")
            return

        # (2) authorize
        decision = await self._authorize(
            authorizer=authorizer,
            tool=tool,
            tool_input=tc.input,
            auth_ctx=auth_ctx,
            tc=tc,
            on_permission=on_permission,
        )
        if decision is None:
            # _authorize yielded an error already.
            yield self._error_tuple(tc, "permission check failed")
            return

        from kernel.tool_authz import PermissionAllow

        if not isinstance(decision, PermissionAllow):
            # deny or (after ask round-trip) still deny.
            message = getattr(decision, "message", "tool call denied")
            yield self._error_tuple(tc, message)
            return

        # Apply updated_input if authorizer chose to rewrite.
        effective_input = decision.updated_input or tc.input

        # (3) pre_tool_use hook — may veto (HookBlock) or rewrite
        # ``ctx.tool_input``.  Skipped when ``deps.hooks`` is None.
        blocked, pre_ctx = await self._fire_hook(
            event=HookEvent.PRE_TOOL_USE,
            mode=mode,
            tool_name=tool.name,
            tool_input=dict(effective_input),
        )
        if blocked:
            yield self._error_tuple(tc, "pre_tool_use hook blocked execution")
            return
        # Honour hook rewrites when the event spec allows input mutation.
        effective_input = pre_ctx.tool_input or effective_input

        # (4) ToolCallStart event.
        title = tool.user_facing_name(effective_input)
        raw_input_json: str | None = None
        try:
            raw_input_json = orjson.dumps(effective_input).decode()[:2000]
        except (TypeError, ValueError):
            raw_input_json = None
        yield (
            ToolCallStart(id=tc.id, title=title, kind=tool.kind, raw_input=raw_input_json),
            None,
        )

        # (5) execute
        final_result = None
        try:
            async for event in tool.call(effective_input, tool_ctx):
                from kernel.tools.types import ToolCallProgress as TP
                from kernel.tools.types import ToolCallResult as TR

                if isinstance(event, TR):
                    final_result = event
                elif isinstance(event, TP):
                    if event.passthrough_event is not None:
                        # AgentTool: transparently forward sub-agent events
                        yield (event.passthrough_event, None)
                    else:
                        yield (
                            ToolCallProgressEvent(id=tc.id, content=list(event.content)),
                            None,
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("tool %s execution failed", tool.name)
            # (6') post_tool_failure hook — tells observers the tool crashed.
            await self._fire_hook(
                event=HookEvent.POST_TOOL_FAILURE,
                mode=mode,
                tool_name=tool.name,
                tool_input=dict(effective_input),
                error_message=str(exc),
            )
            yield self._error_tuple(tc, f"tool execution failed: {exc}")
            return

        if final_result is None:
            yield self._error_tuple(tc, "tool produced no result")
            return

        # (6) post_tool_use hook — after successful execution; handlers
        # see the final LLM-facing output and can queue reminders.
        coerced = _coerce_content(final_result.llm_content)
        await self._fire_hook(
            event=HookEvent.POST_TOOL_USE,
            mode=mode,
            tool_name=tool.name,
            tool_input=dict(effective_input),
            tool_output=coerced if isinstance(coerced, str) else None,
        )

        # (6.6a) context_modifier — tools like EnterWorktree/ExitWorktree
        # return a modifier that updates session-level state (cwd, env).
        # Applied via on_context_changed callback to the Orchestrator.
        if final_result.context_modifier is not None:
            try:
                new_ctx = final_result.context_modifier(tool_ctx)
                if self._on_context_changed is not None:
                    self._on_context_changed(new_ctx)
            except Exception:
                logger.exception("context_modifier for %s failed", tool.name)

        # (6.6) skill dynamic discovery — file-mutating tools trigger
        # SkillManager.on_file_touched() to discover nested skill dirs
        # and activate conditional (paths-filtered) skills.
        if tool.name in _FILE_MUTATING_TOOLS:
            await self._notify_file_touched(tool.name, effective_input)

        # (6.5) tool-result budget — truncate oversized results before
        # they enter conversation history (STEP 1 layer 1a).
        coerced = _apply_result_budget(coerced, tool.max_result_size_chars)

        # (7) emit ToolCallResult + ToolResultContent.
        yield (
            ToolCallResultEvent(id=tc.id, content=list(final_result.llm_content)),
            ToolResultContent(
                tool_use_id=tc.id,
                content=coerced,
                is_error=False,
            ),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _fire_hook(
        self,
        *,
        event: HookEvent,
        mode: Literal["default", "plan", "bypass"],
        tool_name: str | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_output: str | None = None,
        error_message: str | None = None,
    ) -> tuple[bool, HookEventCtx]:
        """Fire ``event`` through ``deps.hooks`` and drain reminders.

        Returns ``(blocked, ctx)``.  When ``deps.hooks`` is ``None``
        (HookManager unavailable) this is a no-op fire that always
        returns ``(False, <empty ctx>)`` — callers still get a ctx for
        uniform code paths (reading ``ctx.tool_input`` after a no-op
        fire just returns the original value).
        """
        ambient = AmbientContext(
            session_id=self._session_id,
            cwd=self._cwd,
            agent_depth=self._agent_depth,
            mode=mode,
            timestamp=time.time(),
        )
        ctx = HookEventCtx(
            event=event,
            ambient=ambient,
            tool_name=tool_name,
            tool_input=dict(tool_input) if tool_input else {},
            tool_output=tool_output,
            error_message=error_message,
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

    async def _authorize(
        self,
        *,
        authorizer: ToolAuthorizer | None,
        tool: Tool,
        tool_input: dict[str, Any],
        auth_ctx: AuthorizeContext,
        tc: ToolUseContent,
        on_permission: PermissionCallback,
    ) -> PermissionDecision | None:
        """Run the authorize → optional on_permission round-trip.

        The ``on_permission`` call is wrapped in ``_permission_lock`` so
        that concurrent tools never show two permission prompts at once.

        Returns:
          - PermissionAllow on approval (either direct or after ask),
          - PermissionDeny when rejected,
          - None if the authorizer itself crashed (rare — the authorizer
            fail-closes to deny internally, so this path is defensive
            only and maps to "deny" at the caller).
        """
        if authorizer is None:
            # Degraded mode: allow-all fallback.
            if not self._allow_all_warned:
                logger.warning(
                    "ToolExecutor: ToolAuthorizer unavailable — allowing all tool calls (degraded)"
                )
                self._allow_all_warned = True
            from kernel.tool_authz import PermissionAllow
            from kernel.tool_authz.types import ReasonFailClosed

            return PermissionAllow(
                decision_reason=ReasonFailClosed(error_class="authorizer_unavailable"),
            )

        try:
            decision = await authorizer.authorize(tool=tool, tool_input=tool_input, ctx=auth_ctx)
        except Exception:
            logger.exception("ToolAuthorizer.authorize raised — treating as deny")
            return None

        from kernel.tool_authz import PermissionAsk

        if not isinstance(decision, PermissionAsk):
            return decision

        # Ask round-trip via Session layer — serialized across concurrent tools.
        risk: str = "medium"
        for item in (getattr(decision, "decision_reason", None),):
            if item is not None:
                maybe_risk = getattr(item, "risk", None)
                if maybe_risk in ("low", "medium", "high"):
                    risk = maybe_risk  # type: ignore[assignment]
        req = PermissionRequest(
            tool_use_id=tc.id,
            tool_name=tool.name,
            tool_title=tool.user_facing_name(tool_input),
            input_summary=decision.message,
            risk_level=risk,  # type: ignore[arg-type]
            tool_input=dict(tool_input),
            options=_permission_options_from_suggestions(decision.suggestions),
        )
        try:
            async with self._permission_lock:
                response: PermissionResponse = await on_permission(req)
        except Exception:
            logger.exception("on_permission raised — treating as reject (no interactive channel)")
            from kernel.tool_authz import PermissionDeny
            from kernel.tool_authz.types import ReasonNoPrompt

            return PermissionDeny(
                message="no interactive channel available",
                decision_reason=ReasonNoPrompt(),
            )

        if response.decision == "reject":
            from kernel.tool_authz import PermissionDeny
            from kernel.tool_authz.types import ReasonFailClosed

            return PermissionDeny(
                message="user rejected permission request",
                decision_reason=ReasonFailClosed(error_class="user_reject"),
            )

        # allow_always: also record a session grant.
        if response.decision == "allow_always":
            try:
                authorizer.grant(tool=tool, tool_input=tool_input, ctx=auth_ctx)
            except Exception:
                logger.exception("authorizer.grant failed — allowing this call anyway")

        from kernel.tool_authz import PermissionAllow
        from kernel.tool_authz.types import ReasonFailClosed

        return PermissionAllow(
            decision_reason=ReasonFailClosed(error_class="user_allow"),
            updated_input=response.updated_input,
        )

    async def _notify_file_touched(self, tool_name: str, tool_input: dict[str, Any]) -> None:
        """Notify SkillManager that file-mutating tools ran.

        Extracts file paths from tool input and calls
        ``skills.on_file_touched()`` for dynamic skill discovery and
        conditional skill activation.
        """
        skills = self._deps.skills
        if skills is None:
            return

        # Extract file path from tool input (FileEdit / FileWrite).
        file_path = tool_input.get("file_path") or tool_input.get("path")
        if not file_path or not isinstance(file_path, str):
            return

        try:
            await skills.on_file_touched([file_path], str(self._cwd))
        except Exception:
            logger.debug("skills.on_file_touched failed — non-fatal", exc_info=True)

    def _build_tool_context(self, tool_source: ToolManager | None) -> ToolContext:
        from kernel.tools.context import ToolContext
        from kernel.tools.file_state import FileStateCache

        file_state = (
            tool_source.file_state()
            if tool_source is not None and hasattr(tool_source, "file_state")
            else FileStateCache()
        )
        # Compute interactive signal for EnterPlanModeTool (Gap 13).
        interactive = True
        provider = self._deps.should_avoid_prompts_provider
        if provider is not None:
            try:
                interactive = not bool(provider())
            except Exception:
                pass

        # fire_hook closure — bridges Tool.call to HookManager without
        # requiring Tools to import the hook subsystem directly.
        # HookManager.fire(ctx) reads ctx.event internally; the tool
        # already sets ctx.event before calling, so the ``event`` param
        # at the tool-context boundary is informational only.
        hooks = self._deps.hooks
        fire_hook_fn = None
        if hooks is not None:

            async def _fire_hook(event: Any, event_ctx: Any) -> bool:
                # event is already baked into event_ctx.event; pass ctx only.
                return await hooks.fire(event_ctx)

            fire_hook_fn = _fire_hook

        return ToolContext(
            session_id=self._session_id,
            agent_depth=self._agent_depth,
            agent_id=self._agent_id,
            cwd=self._cwd,
            cancel_event=asyncio.Event(),
            file_state=file_state,
            tasks=self._deps.task_registry,
            set_plan_mode=self._set_plan_mode,
            set_mode=self._set_mode,
            interactive=interactive,
            queue_reminders=self._deps.queue_reminders,
            spawn_subagent=self._spawn_subagent,
            deliver_cross_session=self._deps.deliver_cross_session,
            schedule_manager=self._deps.schedule_manager,
            mcp_manager=getattr(self._deps, "mcp", None),
            git_manager=getattr(self._deps, "git", None),
            summarise=getattr(self._deps, "summarise", None),
            fire_hook=fire_hook_fn,
        )

    def _build_authorize_context(self, *, mode: str) -> AuthorizeContext:
        from kernel.tool_authz import AuthorizeContext

        should_avoid = False
        provider = self._deps.should_avoid_prompts_provider
        if provider is not None:
            try:
                should_avoid = bool(provider())
            except Exception:
                logger.debug("should_avoid_prompts_provider raised — defaulting False")

        return AuthorizeContext(
            session_id=self._session_id,
            agent_depth=self._agent_depth,
            mode=mode,  # type: ignore[arg-type]
            cwd=self._cwd,
            connection_auth=self._deps.connection_auth,  # may be None in gateway sessions
            should_avoid_prompts=should_avoid,
        )

    async def _error_unknown_tool(self, tc: ToolUseContent) -> AsyncGenerator[_EventPair, None]:
        yield (ToolCallStart(id=tc.id, title=tc.name, kind=ToolKind.other), None)
        yield self._error_tuple(tc, f"tool {tc.name!r} is not registered")

    def _error_tuple(
        self, tc: ToolUseContent, message: str
    ) -> tuple[OrchestratorEvent, ToolResultContent]:
        return (
            ToolCallError(id=tc.id, error=message),
            ToolResultContent(
                tool_use_id=tc.id,
                content=message,
                is_error=True,
            ),
        )


def _permission_options_from_suggestions(
    suggestions: list[Any],
) -> tuple[PermissionRequestOption, ...]:
    """Project authorizer suggestions into session permission options."""
    options: list[PermissionRequestOption] = []
    for suggestion in suggestions:
        outcome = getattr(suggestion, "outcome", None)
        label = str(getattr(suggestion, "label", "") or "")
        if outcome == "allow_once":
            options.append(
                PermissionRequestOption(
                    option_id="allow_once",
                    name=label or "Allow once",
                    kind="allow_once",
                )
            )
        elif outcome == "allow_always":
            options.append(
                PermissionRequestOption(
                    option_id="allow_always",
                    name=label or "Allow always",
                    kind="allow_always",
                )
            )
        elif outcome == "deny":
            options.append(
                PermissionRequestOption(
                    option_id="reject",
                    name=label or "Reject",
                    kind="reject_once",
                )
            )
    return tuple(options)


def _coerce_content(blocks: list[Any]) -> str | list[Any]:
    """Pack ``list[ContentBlock]`` into a shape the LLM layer accepts."""
    # Anthropic accepts both str and list; we prefer list pass-through
    # when blocks are rich, else join as string.
    if all(isinstance(b, TextContent) or hasattr(b, "text") for b in blocks):
        return "\n".join(getattr(b, "text", "") for b in blocks)
    return list(blocks)


def _apply_result_budget(content: str | list[Any], budget: int) -> str | list[Any]:
    """Truncate a tool result that exceeds ``budget`` characters.

    Only applies to string content; rich (list) content is passed through
    unchanged (individual blocks may be truncated in future layers).
    """
    if not isinstance(content, str):
        return content
    if len(content) <= budget:
        return content
    original_size = len(content)
    return (
        content[:budget] + f"\n\n[tool result truncated — {original_size} chars, "
        f"kept first {budget} chars]"
    )
