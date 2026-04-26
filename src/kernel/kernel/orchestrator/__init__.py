"""Orchestrator — per-session conversation engine.

Not a global subsystem; instantiated per Session by SessionManager.

Public interface
----------------
The :class:`Orchestrator` Protocol is the **only** thing the Session layer
should depend on.  ``StandardOrchestrator`` lives in ``orchestrator.py``
and is an internal implementation detail — Session never imports it directly.

Typical Session usage::

    orc: Orchestrator = StandardOrchestrator(module_table, session_id, ...)

    # Run a prompt turn.
    async for event in orc.query(prompt, on_permission=cb):
        ...  # handle OrchestratorEvent

    # Mutate state between turns (sync, fire-and-forget).
    orc.set_plan_mode(True)
    orc.set_config(OrchestratorConfigPatch(model=ModelRef(provider="anthropic", model="claude-opus-4-6")))

    # Read current state (e.g. to build ACP responses).
    snapshot = orc.config
    in_plan = orc.plan_mode

    # Tear down when Session is destroyed.
    await orc.close()
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncGenerator, Protocol, runtime_checkable

from kernel.llm.config import ModelRef

if TYPE_CHECKING:
    from kernel.orchestrator.events import OrchestratorEvent
    from kernel.orchestrator.types import PermissionCallback, StopReason
    from kernel.protocol.interfaces.contracts.content_block import ContentBlock


# ---------------------------------------------------------------------------
# Config types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrchestratorConfig:
    """User-visible snapshot of the Orchestrator's current configuration.

    Returned by ``Orchestrator.config``.  The Session layer uses this to
    populate ``ConfigOptionChanged`` broadcasts and ACP responses.

    Internal-only parameters (``compaction_threshold``) are **not** included
    here; they are set at construction time and never surfaced to clients.
    ``max_turns`` is caller-controlled via ``query(max_turns=...)``.
    """

    model: ModelRef
    """Active model reference, e.g. ``ModelRef(provider="anthropic", model="claude-opus-4-6")``."""

    temperature: float | None = None
    """Sampling temperature, or ``None`` to use the provider default."""

    streaming_tools: bool = False
    """When ``True``, the Orchestrator starts tool execution while the
    LLM is still streaming (safe tools only).  When ``False`` (default),
    tools are dispatched after the full LLM response has been received.

    Both paths use the same ``ToolExecutor`` interface; this flag only
    changes *when* ``add_tool()`` is called relative to the stream loop.
    """

    language: str | None = None
    """Preferred response language name.  When set, PromptBuilder injects
    CC's ``# Language`` section (prompts.ts:142-149) into every turn's
    system prompt.  Sourced from the ``orchestrator.language`` config
    section (see :class:`kernel.orchestrator.config_section.OrchestratorPrefs`)
    and plumbed through ``OrchestratorConfig`` so ``set_config`` can
    override it per-session.
    """


@dataclass
class OrchestratorConfigPatch:
    """Partial config update applied by ``Orchestrator.set_config()``.

    ``None`` means "leave this field unchanged".
    """

    model: ModelRef | None = None
    temperature: float | None = None
    streaming_tools: bool | None = None
    language: str | None = None


# ---------------------------------------------------------------------------
# Orchestrator Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Orchestrator(Protocol):
    """Contract between the Session layer and the conversation engine.

    The Session layer holds a reference typed as ``Orchestrator`` — it
    never imports or instantiates ``StandardOrchestrator`` directly.
    This keeps the abstraction boundary clean and makes the session layer
    easy to unit-test with a stub.

    Threading / task model
    ~~~~~~~~~~~~~~~~~~~~~~
    ``query()`` runs inside the **caller's** ``asyncio.Task``.  The
    Orchestrator never creates tasks of its own.  Cancellation is delivered
    via ``task.cancel()``; the generator catches it, emits
    :class:`~kernel.orchestrator.events.CancelledEvent`, and returns
    ``StopReason.cancelled`` — giving callers a clean event boundary.

    State mutation timing
    ~~~~~~~~~~~~~~~~~~~~~
    ``set_plan_mode`` and ``set_config`` are synchronous and take effect
    at the **start of the next LLM call** inside the current or a future
    ``query()`` — not mid-stream.  The Orchestrator never emits events in
    response to these calls; the Session layer is responsible for
    broadcasting the resulting state change to connected clients.
    """

    # ── Core ─────────────────────────────────────────────────────────────────

    def query(
        self,
        prompt: list[ContentBlock],
        *,
        on_permission: PermissionCallback,
        token_budget: int | None = None,
        max_turns: int = 0,
    ) -> AsyncGenerator[OrchestratorEvent, None]:
        """Run one prompt turn.

        Drives the ``LLM → tool execution → feed results → LLM`` loop until
        the model stops calling tools, yielding :class:`OrchestratorEvent`
        values as they occur.

        The generator's *return value* (not a yielded value) is the
        :class:`~kernel.orchestrator.types.StopReason` that ended the turn.
        Callers retrieve it via ``StopReason = await gen.aclose()`` or by
        catching ``StopAsyncIteration``::

            gen = orc.query(prompt, on_permission=cb)
            async for event in gen:
                handle(event)
            # gen.ag_return holds the StopReason after the loop.

        Parameters
        ----------
        prompt:
            The user's message content blocks.
        on_permission:
            Called (and awaited) each time a tool requires explicit user
            approval before execution.  The Session layer provides the
            concrete implementation; the Orchestrator is unaware of
            WebSockets or ACP.
        max_turns:
            Caller-controlled limit on LLM ↔ tool iterations.
            ``0`` (default) = unlimited.

        Cancellation
        ------------
        Cancel the enclosing ``asyncio.Task``.  The generator catches
        ``CancelledError``, yields a final
        :class:`~kernel.orchestrator.events.CancelledEvent`, then returns
        ``StopReason.cancelled`` — **without re-raising** — so the caller's
        ``async for`` loop exits cleanly.
        """
        ...

    async def close(self) -> None:
        """Tear down the Orchestrator.

        Cancels any in-progress ``query()`` and releases provider connections.
        Called by the Session layer when the session is destroyed.

        Must be idempotent (safe to call more than once).
        """
        ...

    # ── State mutation (sync, fire-and-forget) ────────────────────────────────

    def set_plan_mode(self, enabled: bool) -> None:
        """Enable or disable plan mode.

        Takes effect at the start of the **next LLM call** — not mid-stream.
        Does not emit any event.  The Session layer is responsible for
        broadcasting ``ModeChanged`` to connected clients after calling this.
        """
        ...

    def set_mode(self, mode: str) -> None:
        """Set the permission mode.

        Accepts ``default`` / ``plan`` / ``bypass`` / ``accept_edits`` /
        ``auto`` / ``dont_ask``.  Implementations validate the literal.
        """
        ...

    def set_config(self, patch: OrchestratorConfigPatch) -> None:
        """Apply a partial config update (model, provider, temperature, …).

        ``None`` fields in *patch* are left unchanged.
        Takes effect at the start of the **next LLM call** — not mid-stream.
        Does not emit any event.  The Session layer is responsible for
        broadcasting ``ConfigOptionChanged`` to connected clients after
        calling this.
        """
        ...

    # ── State reads ───────────────────────────────────────────────────────────

    @property
    def plan_mode(self) -> bool:
        """Current plan mode state.

        The Session layer reads this to build ACP responses (e.g. the
        ``modeId`` field in ``SetSessionModeResponse``).
        """
        ...

    @property
    def stop_reason(self) -> StopReason:
        """The stop reason from the most recent ``query()`` call.

        Only meaningful after the ``async for`` loop over ``query()`` has
        finished.  Defaults to ``end_turn`` before any query is made.

        Python async generators cannot use ``return value``, so
        ``StopReason`` is communicated via this property rather than as
        a generator return value.  The Session layer reads this after
        draining the event stream to build the ACP ``session/prompt``
        response.
        """
        ...

    @property
    def config(self) -> OrchestratorConfig:
        """Current user-visible config snapshot.

        The Session layer reads this to populate ``ConfigOptionChanged``
        broadcasts and to respond to ``session/set_config_option``.
        """
        ...

    @property
    def last_turn_usage(self) -> tuple[int, int]:
        """``(input_tokens, output_tokens)`` accumulated during the last turn.

        Accumulates across all LLM calls within a single ``query()`` call
        (the tool loop may trigger multiple streaming requests).  Reset to
        ``(0, 0)`` at the start of each new ``query()`` call.  Safe to read
        after the ``async for`` loop completes.  Returns ``(0, 0)`` before
        any query has been made.
        """
        ...


# ---------------------------------------------------------------------------
# Public re-exports
# ---------------------------------------------------------------------------

from kernel.orchestrator.events import (  # noqa: E402
    AvailableCommandsChanged,
    CancelledEvent,
    CompactionEvent,
    ConfigOptionChanged,
    ModeChanged,
    OrchestratorEvent,
    PlanUpdate,
    QueryError,
    SessionInfoChanged,
    SubAgentEnd,
    SubAgentStart,
    TextDelta,
    ThoughtDelta,
    ToolCallDiff,
    ToolCallError,
    ToolCallLocations,
    ToolCallProgress,
    ToolCallResult,
    ToolCallStart,
    UserPromptBlocked,
)
from kernel.orchestrator.types import (  # noqa: E402
    LLMProvider,
    OrchestratorDeps,
    PermissionCallback,
    PermissionRequest,
    PermissionResponse,
    StopReason,
    ToolKind,
)

__all__ = [
    # Protocol + config
    "Orchestrator",
    "OrchestratorConfig",
    "OrchestratorConfigPatch",
    # Deps / provider protocol
    "OrchestratorDeps",
    "LLMProvider",
    # Events
    "OrchestratorEvent",
    "TextDelta",
    "ThoughtDelta",
    "ToolCallStart",
    "ToolCallProgress",
    "ToolCallResult",
    "ToolCallError",
    "ToolCallDiff",
    "ToolCallLocations",
    "PlanUpdate",
    "ModeChanged",
    "ConfigOptionChanged",
    "SessionInfoChanged",
    "AvailableCommandsChanged",
    "SubAgentStart",
    "SubAgentEnd",
    "CompactionEvent",
    "QueryError",
    "UserPromptBlocked",
    "CancelledEvent",
    # Types
    "StopReason",
    "ToolKind",
    "PermissionRequest",
    "PermissionResponse",
    "PermissionCallback",
]
