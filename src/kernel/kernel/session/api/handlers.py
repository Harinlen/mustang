"""ACP entry points implemented by SessionManager.

Each public method (``new``, ``load``, ``list``, ``prompt``, ``set_mode``,
``set_config_option``, ``cancel``) maps directly to one ACP request kind.
The mixin owns request → session lookup, queueing, and the side-effects
that must be persisted as ``SessionEvent`` rows or broadcast to clients.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kernel.protocol.acp.schemas.updates import ConfigOptionUpdate, CurrentModeUpdate
from kernel.protocol.interfaces.contracts.cancel_params import CancelParams
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.contracts.list_sessions_params import ListSessionsParams
from kernel.protocol.interfaces.contracts.list_sessions_result import (
    ListSessionsResult,
    SessionSummary,
)
from kernel.protocol.interfaces.contracts.load_session_params import LoadSessionParams
from kernel.protocol.interfaces.contracts.load_session_result import LoadSessionResult
from kernel.protocol.interfaces.contracts.new_session_params import NewSessionParams
from kernel.protocol.interfaces.contracts.new_session_result import NewSessionResult
from kernel.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.protocol.interfaces.contracts.prompt_result import PromptResult
from kernel.protocol.interfaces.contracts.set_config_option_params import (
    SetConfigOptionParams,
)
from kernel.protocol.interfaces.contracts.set_config_option_result import (
    ConfigOptionValue,
    SetConfigOptionResult,
)
from kernel.protocol.interfaces.contracts.set_mode_params import SetModeParams
from kernel.protocol.interfaces.contracts.set_mode_result import SetModeResult
from kernel.protocol.interfaces.errors import InternalError, ResourceNotFoundError
from kernel.session._shared.base import _SessionMixinBase
from kernel.session.events import (
    ConfigOptionChangedEvent,
    ModeChangedEvent,
    SessionLoadedEvent,
)
from kernel.session.models import ConversationRecord
from kernel.session.runtime.helpers import (
    config_list as _config_list,
    decode_cursor as _decode_cursor,
    encode_cursor as _encode_cursor,
    get_git_branch as _get_git_branch,
)
from kernel.session.runtime.state import Session

UTC = timezone.utc
logger = logging.getLogger("kernel.session")


class SessionHandlerMixin(_SessionMixinBase):
    """ACP request handlers — one method per ``session/*`` request kind."""

    def _bind_connection_to_session(self, ctx: HandlerContext, session: Session) -> None:
        """Pin the WebSocket connection to ``session`` for routing + broadcasts.

        Args:
            ctx: Handler context whose ``conn`` and ``sender`` are bound.
            session: Session that will route updates through ``ctx.sender``.
        """
        ctx.conn.bound_session_id = session.session_id
        session.senders[ctx.conn.auth.connection_id] = ctx.sender

    async def new(self, ctx: HandlerContext, params: NewSessionParams) -> NewSessionResult:
        """Handle ACP ``session/new``: mint a session id and create the runtime.

        Args:
            ctx: Handler context for the requesting connection.
            params: ACP request body — ``cwd``, ``mcp_servers``, optional
                ``meta`` (for worktree setup).

        Returns:
            ``NewSessionResult`` carrying the new ``session_id``.
        """
        session_id = str(uuid.uuid4())
        cwd = Path(params.cwd)

        cwd = await self._maybe_create_worktree_session(session_id, cwd, params.meta)

        git_branch = _get_git_branch(cwd)
        session = await self._create_session(
            session_id=session_id,
            cwd=cwd,
            git_branch=git_branch,
            mcp_servers=params.mcp_servers,
        )
        self._bind_connection_to_session(ctx, session)

        return NewSessionResult(session_id=session_id)

    async def _maybe_create_worktree_session(
        self,
        session_id: str,
        cwd: Path,
        meta: dict[str, Any] | None,
    ) -> Path:
        """Allocate a git worktree for this session if ``meta`` requests one.

        Args:
            session_id: Owning session id, recorded in the worktree registry.
            cwd: Working directory the session would otherwise use.
            meta: ``params.meta`` — ``meta["worktree"]`` carries ``slug`` and
                optional ``sparse_paths``.

        Returns:
            The new worktree path, or ``cwd`` unchanged when no worktree was
            requested, the Git subsystem is unavailable, or setup failed.
        """
        worktree_meta = (meta or {}).get("worktree")
        if not worktree_meta:
            return cwd
        try:
            from kernel.git import GitManager
            from kernel.git.types import WorktreeSession
            from kernel.git.worktree import (
                create_worktree,
                find_git_root,
                setup_sparse_checkout,
                validate_slug,
            )

            git = self._module_table.get(GitManager)
            if not git.available:
                return cwd
            slug = worktree_meta["slug"]
            validate_slug(slug)
            root = await find_git_root(git, cwd)
            worktree_path, branch = await create_worktree(git, root, slug)
            if sparse_paths := worktree_meta.get("sparse_paths"):
                await setup_sparse_checkout(git, worktree_path, sparse_paths)
            await git.register_worktree(
                WorktreeSession(
                    session_id=session_id,
                    original_cwd=cwd,
                    worktree_path=worktree_path,
                    worktree_branch=branch,
                    slug=slug,
                    created_at=datetime.now(UTC),
                )
            )
            return worktree_path
        except (KeyError, ImportError):
            return cwd
        except Exception:
            logger.exception(
                "Worktree startup failed for session %s — using original cwd",
                session_id,
            )
            return cwd

    async def load_session(
        self, ctx: HandlerContext, params: LoadSessionParams
    ) -> LoadSessionResult:
        """Handle ACP ``session/load``: attach the connection and replay history.

        Reloads the session from disk if it was evicted, binds the new
        connection, replays the persisted event log so the client sees the
        full transcript, then appends a ``SessionLoadedEvent`` marker.

        Args:
            ctx: Handler context for the joining connection.
            params: ACP request body carrying ``session_id``.

        Returns:
            Empty ``LoadSessionResult`` once the replay completes.

        Raises:
            ResourceNotFoundError: ``params.session_id`` is not in the DB.
        """
        session_id = params.session_id

        record = await self._store.get_session(session_id)
        if record is None:
            raise ResourceNotFoundError(f"Session not found: {session_id!r}")

        if session_id not in self._sessions:
            await self._load_from_disk(session_id)

        session = self._sessions[session_id]
        self._bind_connection_to_session(ctx, session)

        events = await self._store.read_events(session_id)
        for event in events:
            await self._replay_event(ctx, session, event)

        await self._write_event(session, SessionLoadedEvent)

        return LoadSessionResult()

    def _cursor_start_index(
        self,
        records: list[ConversationRecord],
        cursor: str | None,
    ) -> int:
        """Return the index of the first record strictly after ``cursor``.

        Args:
            records: Sessions ordered by ``modified`` DESC then ``session_id``
                DESC (the shape ``_encode_cursor`` produced).
            cursor: Opaque cursor returned by a previous ``list`` call,
                or ``None`` for the first page.

        Returns:
            ``0`` when ``cursor`` is missing, malformed, or already past
            every record — the caller pages from the start in those cases.
        """
        if cursor is None:
            return 0

        try:
            cursor_modified, cursor_id = _decode_cursor(cursor)
        except Exception:
            logger.warning("Invalid cursor — returning from beginning")
            return 0

        for index, record in enumerate(records):
            record_is_after_cursor = record.modified < cursor_modified or (
                record.modified == cursor_modified and record.session_id < cursor_id
            )
            if record_is_after_cursor:
                return index
        return 0

    def _list_page(
        self,
        records: list[ConversationRecord],
        *,
        cursor: str | None,
    ) -> tuple[list[ConversationRecord], str | None]:
        """Slice one page of size ``list_page_size`` from ``records``.

        Args:
            records: Pre-sorted list to page through.
            cursor: Cursor from the previous page, or ``None`` for the start.

        Returns:
            ``(page, next_cursor)``.  ``next_cursor`` is ``None`` when this
            slice already reached the end of ``records``.
        """
        start = self._cursor_start_index(records, cursor)
        page = records[start : start + self._flags.list_page_size]
        if start + self._flags.list_page_size >= len(records):
            return page, None

        last_record = page[-1]
        return page, _encode_cursor(last_record.modified, last_record.session_id)

    @staticmethod
    def _session_summaries(records: list[ConversationRecord]) -> list[SessionSummary]:
        return [
            SessionSummary(
                session_id=record.session_id,
                cwd=record.cwd,
                created_at=record.created,
                title=record.title,
            )
            for record in records
        ]

    async def list(self, ctx: HandlerContext, params: ListSessionsParams) -> ListSessionsResult:
        """Handle ACP ``session/list``: paginated session summaries.

        Args:
            ctx: Handler context (unused beyond signature parity).
            params: ACP request body — optional ``cwd`` filter and
                opaque ``cursor`` for pagination.

        Returns:
            ``ListSessionsResult`` with one page of summaries plus the
            ``next_cursor`` (``None`` on the last page).
        """
        records = await self._store.list_sessions()

        if params.cwd:
            records = [record for record in records if record.cwd == params.cwd]

        page, next_cursor = self._list_page(records, cursor=params.cursor)
        return ListSessionsResult(sessions=self._session_summaries(page), next_cursor=next_cursor)

    async def prompt(self, ctx: HandlerContext, params: PromptParams) -> PromptResult:
        """Handle ACP ``session/prompt``: run the turn now or queue it.

        When the session is idle the turn runs synchronously inside the
        request task.  Otherwise it joins the FIFO and the response is
        delivered via the queued turn's response future.

        Args:
            ctx: Handler context — ``request_id`` is recorded with the turn.
            params: ACP request body — ``session_id``, ``prompt`` blocks,
                optional ``max_turns``.

        Returns:
            ``PromptResult`` with the turn's stop reason.

        Raises:
            ResourceNotFoundError: session is not in memory.
            InternalError: queue depth has reached ``max_queue_length``.
        """
        session = self._get_or_raise(params.session_id)

        if session.in_flight_turn is None and not session.queue:
            return await self._run_turn_core(session, params, ctx.request_id)

        if len(session.queue) >= self._flags.max_queue_length:
            raise InternalError("session prompt queue full")

        return await self._enqueue_turn(session, params, request_id=ctx.request_id)

    async def set_mode(self, ctx: HandlerContext, params: SetModeParams) -> SetModeResult:
        """Handle ACP ``session/set_mode``: switch the session mode and notify.

        Args:
            ctx: Handler context (unused beyond signature parity).
            params: ACP request body — ``session_id`` and the new ``mode_id``.

        Returns:
            Empty ``SetModeResult`` once the change is persisted and broadcast.

        Raises:
            ResourceNotFoundError: session is not in memory.
        """
        session = self._get_or_raise(params.session_id)
        old_mode = session.mode_id
        session.mode_id = params.mode_id
        session.orchestrator.set_mode(params.mode_id)

        await self._write_event(
            session,
            ModeChangedEvent,
            mode_id=params.mode_id,
            from_mode=old_mode,
        )
        await self._broadcast(session, CurrentModeUpdate(mode_id=params.mode_id))
        return SetModeResult()

    async def set_config_option(
        self, ctx: HandlerContext, params: SetConfigOptionParams
    ) -> SetConfigOptionResult:
        """Handle ACP ``session/set_config_option``: update, persist, broadcast.

        Args:
            ctx: Handler context (unused beyond signature parity).
            params: ACP request body — ``session_id``, ``config_id``, ``value``.

        Returns:
            ``SetConfigOptionResult`` echoing the full config snapshot
            after the change.

        Raises:
            ResourceNotFoundError: session is not in memory.
        """
        session = self._get_or_raise(params.session_id)
        session.config_options[params.config_id] = params.value

        full_state = dict(session.config_options)
        await self._write_event(
            session,
            ConfigOptionChangedEvent,
            config_id=params.config_id,
            value=params.value,
            full_state=full_state,
        )
        await self._broadcast(session, ConfigOptionUpdate(config_options=_config_list(full_state)))
        return SetConfigOptionResult(
            config_options=[ConfigOptionValue(**item) for item in _config_list(full_state)]
        )

    async def cancel(self, ctx: HandlerContext, params: CancelParams) -> None:
        """Handle ACP ``session/cancel``: stop the in-flight turn and drop the queue.

        ACP cancellation is a notification, not a request — unknown sessions
        are silently ignored rather than raising.  Queued turns resolve
        immediately with ``stop_reason="cancelled"``; the running turn's
        own ``finally`` block clears ``in_flight_turn``, so eviction is
        scheduled rather than performed inline.

        Args:
            ctx: Handler context (unused beyond signature parity).
            params: ACP notification body carrying ``session_id``.
        """
        session = self._sessions.get(params.session_id)
        if session is None:
            return

        if session.in_flight_turn is not None:
            session.in_flight_turn.task.cancel()

        while session.queue:
            queued = session.queue.popleft()
            if not queued.response_future.done():
                queued.response_future.set_result(PromptResult(stop_reason="cancelled"))

        asyncio.create_task(self._maybe_evict(session))
