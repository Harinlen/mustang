"""OrchestratorEvent — all events that ``Orchestrator.query()`` can yield.

Every dataclass here is ``frozen=True`` (immutable value objects).
The Session layer pattern-matches on these to build ACP ``session/update``
notifications.

Mapping to ACP is documented in
``docs/kernel/interfaces/protocol.md#会话层事件--sessionupdate-映射``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Union

from kernel.llm.types import Message

if TYPE_CHECKING:
    from kernel.orchestrator.types import StopReason, ToolKind
    from kernel.protocol.interfaces.contracts.content_block import ContentBlock


# ---------------------------------------------------------------------------
# Text / thought streaming
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    """One streaming text chunk from the LLM."""

    content: str


@dataclass(frozen=True)
class ThoughtDelta:
    """One streaming reasoning / extended-thinking chunk from the LLM."""

    content: str


# ---------------------------------------------------------------------------
# Tool call lifecycle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolCallStart:
    """Emitted once when the Orchestrator begins executing a tool call."""

    id: str
    """Matches the ``tool_use_id`` from the LLM response."""

    title: str
    """Human-readable display name shown in the UI (e.g. ``"Read file"``)."""

    kind: ToolKind
    """Semantic category; determines concurrency and plan-mode eligibility."""

    raw_input: str | None = None
    """Raw JSON input string, included for debug/tracing purposes only."""


@dataclass(frozen=True)
class ToolCallProgress:
    """Zero or more progress events emitted while a tool is running."""

    id: str
    """Matches the ``id`` of the preceding :class:`ToolCallStart`."""

    content: list[ContentBlock]
    """Partial output produced so far."""


@dataclass(frozen=True)
class ToolCallResult:
    """Emitted once when a tool call completes successfully."""

    id: str
    content: list[ContentBlock]
    """Final tool output, fed back to the LLM as a ``tool_result`` block."""


@dataclass(frozen=True)
class ToolCallError:
    """Emitted when a tool call fails (execution error, rejection, or
    plan-mode block).  The ``error`` string is already formatted for LLM
    consumption."""

    id: str
    error: str


@dataclass(frozen=True)
class ToolCallDiff:
    """Optional companion to :class:`ToolCallResult` for file-editing tools.

    Carries a before/after diff so the UI can show a change preview.
    """

    id: str
    path: str
    """Absolute or workspace-relative file path."""

    old_text: str | None
    """``None`` when the file was newly created."""

    new_text: str


@dataclass(frozen=True)
class ToolCallLocations:
    """Optional companion to :class:`ToolCallResult` for tools that produce
    navigable source locations (e.g. Grep, Glob).

    Enables "follow the agent" cursor jumps in the IDE extension.
    """

    id: str
    locations: list[dict[str, Any]]
    """Each entry is a ``FileLocation``-shaped dict:
    ``{"path": str, "line": int | None, "column": int | None}``.
    Using ``dict`` here avoids pulling a heavy type into the interface;
    the Session layer validates shape when building ACP frames.
    """


# ---------------------------------------------------------------------------
# Session / UI state changes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanUpdate:
    """Full snapshot of the current plan (not a diff).

    Emitted whenever the plan changes during a ``query()`` turn.
    """

    entries: list[dict[str, Any]]
    """Each entry is a ``PlanEntry``-shaped dict:
    ``{"id": str, "title": str, "status": str}``.
    """


@dataclass(frozen=True)
class ModeChanged:
    """Emitted when plan mode is toggled."""

    mode_id: str
    """``"plan"`` or ``"normal"``."""


@dataclass(frozen=True)
class ConfigOptionChanged:
    """Full snapshot of the current user-visible config (not a diff).

    Emitted when the Orchestrator config changes mid-session.
    """

    options: dict[str, Any]


@dataclass(frozen=True)
class SessionInfoChanged:
    """Partial update for session metadata visible to the client."""

    title: str | None = None
    """New session title, or ``None`` if unchanged."""


@dataclass(frozen=True)
class AvailableCommandsChanged:
    """Emitted when the set of available slash commands changes."""

    commands: list[dict[str, Any]]
    """Each entry is an ``AvailableCommand``-shaped dict:
    ``{"name": str, "description": str}``.
    """


# ---------------------------------------------------------------------------
# Sub-agent bracketing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubAgentStart:
    """Marks the beginning of a sub-agent's event stream inside the parent.

    All events between :class:`SubAgentStart` and :class:`SubAgentEnd`
    originate from the sub-agent.  The Session layer uses this to
    route them to a separate JSONL file.
    """

    agent_id: str
    """Unique identifier; matches the ``agent-<id>`` aux directory name."""

    description: str
    agent_type: str
    """e.g. ``"Explore"``, ``"general-purpose"``."""

    spawned_by_tool_id: str
    """The ``ToolCallStart.id`` of the AgentTool call that created this agent."""


@dataclass(frozen=True)
class SubAgentEnd:
    """Marks the end of a sub-agent's event stream."""

    agent_id: str
    stop_reason: StopReason
    transcript: list[Any] | None = None
    """Conversation history from the sub-agent, captured for potential
    resume via SendMessage.  ``None`` when transcript capture is
    disabled or the sub-agent produced no history."""


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CompactionEvent:
    """Emitted after the Orchestrator compacts the conversation history."""

    tokens_before: int
    tokens_after: int


@dataclass(frozen=True)
class QueryError:
    """A provider-level error surfaced to the Session layer.

    Emitted when the LLM provider yields a ``StreamError`` (transient
    failure: rate limit, temporary outage) or raises ``ProviderError``
    (unrecoverable: auth failure, bad config).

    After a ``QueryError`` the generator returns ``StopReason.error``.
    Distinct from ``ToolCallError``, which signals a tool execution failure.
    """

    message: str
    code: str | None = None
    """Provider error code, e.g. ``"rate_limit_error"``.  ``None`` when
    the provider did not supply one."""


@dataclass(frozen=True)
class UserPromptBlocked:
    """Emitted when the ``user_prompt_submit`` hook vetoes the query.

    After this event the generator returns ``StopReason.hook_blocked``.
    The user's prompt is **not** appended to conversation history and
    the LLM is never called.
    """

    reason: str = ""
    """The ``HookBlock.reason`` string from the handler that blocked."""


@dataclass(frozen=True)
class CancelledEvent:
    """The final event in a cancelled ``query()`` stream.

    After this event the generator returns ``StopReason.cancelled``.
    No further events will be yielded.
    """


# ---------------------------------------------------------------------------
# History persistence (Session layer only — not broadcast to clients)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryAppend:
    """Emitted after a Message is appended to ConversationHistory.

    Used exclusively by the Session layer for lossless history persistence.
    Not broadcast to clients (UI events already cover streaming display).
    """

    message: Message


@dataclass(frozen=True)
class HistorySnapshot:
    """Emitted after compaction replaces the conversation history.

    On resume, this event replaces all prior ``HistoryAppend`` messages
    with the compacted snapshot.
    """

    messages: list[Message]


# ---------------------------------------------------------------------------
# Union type
# ---------------------------------------------------------------------------

OrchestratorEvent = Union[
    TextDelta,
    ThoughtDelta,
    ToolCallStart,
    ToolCallProgress,
    ToolCallResult,
    ToolCallError,
    ToolCallDiff,
    ToolCallLocations,
    PlanUpdate,
    ModeChanged,
    ConfigOptionChanged,
    SessionInfoChanged,
    AvailableCommandsChanged,
    SubAgentStart,
    SubAgentEnd,
    CompactionEvent,
    QueryError,
    UserPromptBlocked,
    CancelledEvent,
    HistoryAppend,
    HistorySnapshot,
]
"""Discriminated union of every event ``Orchestrator.query()`` can yield.

Session layer consumers should exhaustively match on this union.
"""
