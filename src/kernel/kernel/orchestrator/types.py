"""Orchestrator public types.

Shared across the orchestrator interface and its callers (Session layer).
No implementation logic lives here.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from kernel.hooks import HookManager
    from kernel.memory import MemoryManager
    from kernel.prompts.manager import PromptManager
    from kernel.schedule import ScheduleManager
    from kernel.skills import SkillManager
    from kernel.tasks.registry import TaskRegistry
    from kernel.tool_authz.authorizer import ToolAuthorizer
    from kernel.tools import ToolManager


# ---------------------------------------------------------------------------
# StopReason
# ---------------------------------------------------------------------------


class StopReason(str, Enum):
    """The reason a ``query()`` generator stopped producing events."""

    end_turn = "end_turn"
    """LLM finished normally and made no further tool calls."""

    max_turns = "max_turns"
    """Internal turn limit reached (engine safety cap)."""

    cancelled = "cancelled"
    """The enclosing ``asyncio.Task`` was cancelled by the Session layer."""

    error = "error"
    """Unrecoverable error (e.g. provider outage). A ``ToolCallError`` or
    similar event will have been emitted immediately before this."""

    hook_blocked = "hook_blocked"
    """A blocking hook (e.g. ``user_prompt_submit``) vetoed the query.
    A ``UserPromptBlocked`` event will have been emitted immediately
    before this."""

    budget_exceeded = "budget_exceeded"
    """Cumulative token usage for this query exceeded the caller-supplied
    budget.  A ``QueryError`` with code ``"token_budget_exceeded"`` will
    have been emitted immediately before this."""


# ---------------------------------------------------------------------------
# ToolKind
# ---------------------------------------------------------------------------


class ToolKind(str, Enum):
    """Semantic category of a tool, used to determine concurrency and
    plan-mode eligibility."""

    # Read-only — safe to run concurrently.
    read = "read"
    search = "search"
    fetch = "fetch"
    think = "think"

    # Orchestration — spawns sub-agents; not mutating itself, but not
    # read-only either.  Survives plan-mode filtering so Agent stays
    # visible when the LLM is in plan mode (CC parity).
    orchestrate = "orchestrate"

    # Mutating — run serially, require extra care in plan mode.
    edit = "edit"
    delete = "delete"
    move = "move"
    execute = "execute"
    other = "other"

    @property
    def is_read_only(self) -> bool:
        """True for kinds that are safe to execute concurrently."""
        return self in {
            ToolKind.read,
            ToolKind.search,
            ToolKind.fetch,
            ToolKind.think,
        }


# ---------------------------------------------------------------------------
# Permission round-trip
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionRequest:
    """Sent to the Session layer when a tool requires user approval.

    The Session layer forwards this to the connected client via
    ``session/request_permission`` and waits for a response.
    """

    tool_use_id: str
    """Matches the ``id`` of the ``ToolCallStart`` event."""

    tool_name: str
    """Internal tool identifier (e.g. ``"bash"``)."""

    tool_title: str
    """Human-readable display name (e.g. ``"Run command"``)."""

    input_summary: str
    """One-line description of what the tool is about to do, shown to the
    user in the permission dialog."""

    risk_level: Literal["low", "medium", "high"]

    tool_input: dict[str, Any] | None = None
    """The raw tool input dict.  Included so the client can render
    tool-specific UIs (e.g. ``AskUserQuestionTool`` embeds its
    ``questions`` array here).  ``None`` for tools that don't need it."""


@dataclass(frozen=True)
class PermissionResponse:
    """Returned by the ``PermissionCallback`` after the user decides.

    ``updated_input`` carries data back from the client when the
    permission prompt doubles as a user-interaction form (e.g.
    ``AskUserQuestionTool``).  The ``ToolExecutor`` forwards it into
    ``PermissionAllow.updated_input`` so the tool's ``call()`` receives
    the enriched input (answers, annotations, etc.).
    """

    decision: Literal["allow_once", "allow_always", "reject"]

    updated_input: dict[str, Any] | None = None
    """Optional rewritten tool input returned by the client.

    When present, replaces the original ``tool_use.input`` before the
    tool's ``call()`` method runs.  Used by ``AskUserQuestionTool`` to
    inject the user's answers into the tool input.
    """


# ``PermissionCallback`` is the type of the ``on_permission`` parameter
# passed to ``Orchestrator.query()``.  The Session layer provides the
# concrete implementation; the Orchestrator only knows this signature.
PermissionCallback = Callable[[PermissionRequest], Awaitable[PermissionResponse]]


# ---------------------------------------------------------------------------
# LLMProvider — narrow Protocol consumed by Orchestrator
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMProvider(Protocol):
    """The slice of LLMManager that Orchestrator needs.

    LLMManager satisfies this Protocol at runtime; tests can pass any
    object that implements ``stream()``.
    """

    async def stream(
        self,
        *,
        system: list[Any],  # list[PromptSection]
        messages: list[Any],  # list[Message]
        tool_schemas: list[Any],  # list[ToolSchema]
        model: Any,  # ModelRef
        temperature: float | None,
        thinking: bool = False,
        max_tokens: int | None = None,
    ) -> AsyncGenerator[Any, None]:  # AsyncGenerator[LLMChunk, None]
        """Return an async generator of LLMChunk events for one model call.

        ``model`` is a ``ModelRef(provider, model_id)`` pair.
        ``max_tokens`` overrides the model config value when non-None.
        Used by the Orchestrator for ``max_output_tokens`` escalation.
        """
        ...


# ---------------------------------------------------------------------------
# OrchestratorDeps — all external dependencies, injected at construction
# ---------------------------------------------------------------------------


@dataclass
class OrchestratorDeps:
    """All external dependencies for the Orchestrator.

    Only ``provider`` is required for Phase 1.  All other fields default
    to ``None``; the Orchestrator skips the corresponding feature when
    a dep is absent.

    SessionManager assembles this from ``KernelModuleTable`` and passes it
    to ``StandardOrchestrator.__init__``.  Sub-agents receive the parent's
    deps directly (same provider, same tool registry, etc.).
    """

    provider: LLMProvider
    """Required — routes ``stream()`` calls to the correct LLM backend."""

    tool_source: ToolManager | None = field(default=None)
    """Yields ``ToolSnapshot`` for the current turn."""

    authorizer: ToolAuthorizer | None = field(default=None)
    """Per-tool-call permission decisions.  When absent, ToolExecutor
    falls back to allow-all (aligned with tool-manager.md § Phase 2a)."""

    connection_auth: Any = field(default=None)
    """AuthContext | None — passed into ``AuthorizeContext`` for future
    enterprise IAM checks.  ``None`` when the session is not bound to a
    WS auth context (e.g. gateway-originated sessions)."""

    should_avoid_prompts_provider: Callable[[], bool] | None = field(default=None)
    """Callable | None — returns True when the session cannot route a
    permission prompt to a human (no active WS, non-interactive gateway).
    Used to populate ``AuthorizeContext.should_avoid_prompts`` dynamically
    so sub-agents inherit the root session's interactivity signal
    (aligned with ``docs/plans/landed/tool-authorizer.md`` § 11.5)."""

    memory: MemoryManager | None = field(default=None)
    """Queries relevant memory for prompt injection."""

    skills: SkillManager | None = field(default=None)
    """Provides skill listing for prompt injection and skill activation
    for SkillTool.  When absent, PromptBuilder skips skill listing and
    SkillTool is inoperable."""

    hooks: HookManager | None = field(default=None)
    """In-process hook engine consumed by ``Orchestrator.ToolExecutor``
    (pre_tool_use / post_tool_use / post_tool_failure) and
    ``ToolAuthorizer`` (permission_requested / permission_denied fire
    sites land here too).  When absent, fire-sites silently skip —
    running without hooks is a first-class supported degradation."""

    set_mode: Callable[[str], None] | None = field(default=None)
    """Callable | None — switches the session's permission mode.
    Wired by SessionManager as a closure that synchronously updates
    both ``session.mode_id`` and ``orchestrator._mode``, then enqueues
    a ``ModeChangedEvent`` for deferred writing.  Tools call this via
    ``ToolContext.set_mode`` / ``ToolContext.set_plan_mode``."""

    queue_reminders: Callable[[list[str]], None] | None = field(default=None)
    """Callable | None — accepts ``ctx.messages`` drained from a hook
    fire and queues them onto the owning Session's
    ``pending_reminders`` list.  SessionManager wires this as a closure
    over the session's state; ``None`` means "reminders are dropped"
    (used in degraded mode without a SessionManager).  Aligned with
    docs/plans/landed/hook-manager.md § 6.G (Session-layer ownership)."""

    drain_reminders: Callable[[], list[str]] | None = field(default=None)
    """Callable | None — pops and returns the Session's pending
    system-reminder strings.  Orchestrator calls this at the start of
    each turn; the returned list is prepended to the user prompt as
    ``<system-reminder>`` blocks.  ``None`` means "no reminders to
    drain" (degraded mode or the HookManager subsystem is disabled)."""

    prompts: PromptManager | None = field(default=None)
    """Centralised prompt text store (D18).  When present, subsystems
    read prompt text via ``prompts.get(key)`` / ``prompts.render(key,
    **kwargs)`` instead of loading ``.txt`` files directly."""

    task_registry: TaskRegistry | None = field(default=None)
    """Session-level background task registry.  Used by BashTool
    ``run_in_background`` and AgentTool background mode.  ``None``
    when the task system is unavailable."""

    deliver_cross_session: Callable[[str, str], bool] | None = field(default=None)
    """Callable | None — delivers a message to another session.
    Signature: ``(target_session_id, message) -> success``.
    Wired by SessionManager as a closure over ``deliver_message()``.
    Used by SendMessageTool for ``to="session:<id>"`` addressing."""

    schedule_manager: ScheduleManager | None = field(default=None)
    """Cron scheduling subsystem.  Used by CronCreate/Delete/List tools.
    ``None`` when the schedule subsystem is disabled."""

    git: Any = field(default=None)
    """GitManager subsystem.  Used by PromptBuilder for git context
    injection and by EnterWorktree/ExitWorktree tools.  ``None`` when
    the git subsystem is disabled or unavailable."""

    summarise: Callable[[str, str], Any] | None = field(default=None)
    """Async closure ``(content, user_prompt) -> str`` that summarises
    ``content`` through the ``compact`` role (falls back to ``default``
    when ``compact`` is unconfigured).

    Wired by SessionManager over LLMManager; used by WebFetch
    secondary-model post-processing via ``ToolContext.summarise``.
    ``None`` means "no LLM-driven summarisation available" — callers
    fall back to raw content."""

    mcp_instructions: Callable[[], list[tuple[str, str]]] | None = field(default=None)
    """Sync closure ``() -> list[(server_name, instructions)]`` returning
    connected MCP servers that carry usage instructions.

    Wired by SessionManager as a closure over MCPManager.get_connected();
    only servers with a non-empty ``instructions`` field are included.
    ``None`` means "MCPManager unavailable" — PromptBuilder then omits the
    MCP instructions section."""

    mcp: Any = field(default=None)
    """MCPManager subsystem instance.  Forwarded to ToolContext so that
    ``ListMcpResourcesTool`` and ``ReadMcpResourceTool`` can enumerate and
    read MCP server resources.  ``None`` when the MCP subsystem is
    disabled."""
