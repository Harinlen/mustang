"""Session persistence — async SQLite via SQLAlchemy 2.0.

``SessionStore`` owns one ``sessions.db`` file plus per-session sidecar
directories for tool-result spillover files (large blobs that do not
belong in SQLite).  All mutating DB operations are wrapped in explicit
async transactions; the WAL journal mode is enabled once per connection.

"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from kernel.session.events import SessionEvent, parse_event, serialize_event
from kernel.session.models import (
    ConversationRecord,
    TokenUsageUpdate,
    session_events,
)
from kernel.session.persistence.store_spillover import (
    aux_dir as _aux_dir,
    read_spilled as _read_spilled,
    tool_results_dir as _tool_results_dir,
    write_spilled as _write_spilled,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """Async SQLite-backed store for session records and events.

    Call ``open()`` once before use and ``close()`` on shutdown.  The
    ``sessions.db`` file and ``sessions/`` directory are created automatically
    on first open.

    Tool-result spillover files remain file-based; SQLite is not suitable for
    large blobs.  Their layout is unchanged from the JSONL era.
    """

    def __init__(self, sessions_dir: Path) -> None:
        self._dir = sessions_dir
        self._engine: AsyncEngine | None = None
        self._factory: async_sessionmaker[AsyncSession] | None = None

    async def open(self) -> None:
        """Open (or create) ``sessions.db`` and apply the schema.

        Idempotent: safe to call if the database already exists.
        WAL mode is enabled once per connection via a sync engine event.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        db_path = self._dir / "sessions.db"
        url = f"sqlite+aiosqlite:///{db_path}"
        self._engine = create_async_engine(url, echo=False)

        # WAL mode gives better read/write concurrency for sub-agents.
        @sa.event.listens_for(self._engine.sync_engine, "connect")
        def _enable_wal(dbapi_conn: Any, _record: Any) -> None:
            dbapi_conn.execute("PRAGMA journal_mode = WAL")

        # Late import avoids a circular dependency:
        #   kernel.session.__init__ → store → kernel.session.__init__
        # By the time open() is called all modules are already loaded.
        from kernel.session.migrations import apply as _apply_migrations

        await _apply_migrations(self._engine)

        self._factory = async_sessionmaker(
            self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
        logger.info("SessionStore opened: %s", db_path)

    async def close(self) -> None:
        """Dispose the async engine.  Idempotent."""
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._factory = None
            logger.info("SessionStore closed")

    async def create_session_with_events(
        self,
        record: ConversationRecord,
        events: list[SessionEvent],
    ) -> None:
        """INSERT ConversationRecord + initial events in one transaction.

        Always called with at least a ``SessionCreatedEvent``.  All writes
        are atomic — no orphan rows possible on crash.

        Args:
            record: Session metadata row.  ``created`` / ``modified`` are
                set by the column ``default`` if not already populated.
            events: Initial events to insert alongside the record.
        """
        assert self._factory is not None, "SessionStore.open() not called"
        async with self._factory() as db, db.begin():
            db.add(record)
            for ev in events:
                await db.execute(
                    session_events.insert().values(
                        session_id=ev.session_id,
                        timestamp=ev.timestamp.isoformat(),
                        context=serialize_event(ev).strip(),
                    )
                )

    async def append_event(
        self,
        session_id: str,
        event: SessionEvent,
        tokens: TokenUsageUpdate | None = None,
    ) -> None:
        """Insert one event and optionally update token counters.

        When ``tokens`` is provided, the ``sessions`` row is updated with
        delta increments in the same transaction.  ``modified`` is always
        refreshed alongside any token update.

        Args:
            session_id: Owning session.
            event: Event to persist.
            tokens: Per-turn token deltas to accumulate into session totals.
        """
        assert self._factory is not None, "SessionStore.open() not called"
        async with self._factory() as db, db.begin():
            await db.execute(
                session_events.insert().values(
                    session_id=event.session_id,
                    timestamp=event.timestamp.isoformat(),
                    context=serialize_event(event).strip(),
                )
            )
            if tokens is not None:
                values: dict[str, object] = {"modified": _now_iso()}
                if tokens.input_tokens_delta:
                    values["total_input_tokens"] = (
                        ConversationRecord.total_input_tokens + tokens.input_tokens_delta
                    )
                if tokens.output_tokens_delta:
                    values["total_output_tokens"] = (
                        ConversationRecord.total_output_tokens + tokens.output_tokens_delta
                    )
                await db.execute(
                    sa.update(ConversationRecord)
                    .where(ConversationRecord.session_id == session_id)
                    .values(**values)
                )

    async def update_title(self, session_id: str, title: str) -> None:
        """Set the session title.

        Args:
            session_id: Target session.
            title: New title string.
        """
        assert self._factory is not None, "SessionStore.open() not called"
        async with self._factory() as db, db.begin():
            await db.execute(
                sa.update(ConversationRecord)
                .where(ConversationRecord.session_id == session_id)
                .values(title=title, modified=_now_iso())
            )

    async def delete_session(self, session_id: str) -> None:
        """Delete events then the session row in one atomic transaction.

        Spillover sidecar files are NOT removed here; the caller is
        responsible for cleaning up ``aux_dir(session_id)``.

        Args:
            session_id: Session to delete.
        """
        assert self._factory is not None, "SessionStore.open() not called"
        async with self._factory() as db, db.begin():
            await db.execute(
                session_events.delete().where(session_events.c.session_id == session_id)
            )
            await db.execute(
                sa.delete(ConversationRecord).where(ConversationRecord.session_id == session_id)
            )

    async def read_events(self, session_id: str) -> list[SessionEvent]:
        """Return all events for a session, ordered by timestamp ascending.

        Sub-agent events (``agent_depth > 0``) are interleaved with
        main-session events in wall-clock order.  Malformed rows are
        skipped with a warning.

        Args:
            session_id: Target session.

        Returns:
            List of typed ``SessionEvent`` objects, oldest first.
        """
        assert self._factory is not None, "SessionStore.open() not called"
        stmt = (
            sa.select(session_events.c.context)
            .where(session_events.c.session_id == session_id)
            .order_by(session_events.c.timestamp)
        )
        async with self._factory() as db:
            rows = (await db.execute(stmt)).fetchall()

        result: list[SessionEvent] = []
        for (context,) in rows:
            try:
                result.append(parse_event(context))
            except Exception:
                logger.warning("session=%s: malformed event row — skipping", session_id)
        return result

    async def list_sessions(self) -> list[ConversationRecord]:
        """Return all sessions ordered by ``modified`` DESC.

        Objects are detached from the SQLAlchemy session on return (the
        ``async_sessionmaker`` uses ``expire_on_commit=False``), so
        attribute access is safe after the context manager exits.

        Returns:
            List of ``ConversationRecord`` objects, most-recent first.
        """
        assert self._factory is not None, "SessionStore.open() not called"
        stmt = sa.select(ConversationRecord).order_by(ConversationRecord.modified.desc())
        async with self._factory() as db:
            return list((await db.execute(stmt)).scalars().all())

    async def get_session(self, session_id: str) -> ConversationRecord | None:
        """Return a single session record by primary key, or ``None``.

        Args:
            session_id: Target session.
        """
        assert self._factory is not None, "SessionStore.open() not called"
        async with self._factory() as db:
            return await db.get(ConversationRecord, session_id)

    def aux_dir(self, session_id: str) -> Path:
        """Per-session auxiliary directory (tool results, etc.)."""
        return _aux_dir(self._dir, session_id)

    def tool_results_dir(self, session_id: str) -> Path:
        return _tool_results_dir(self._dir, session_id)

    def write_spilled(self, session_id: str, content: str) -> tuple[str, str]:
        """Write an oversized tool result to a sidecar file.

        Args:
            session_id: Owning session.
            content: Full text content to spill.

        Returns:
            ``(relative_path, result_hash)`` where ``relative_path`` is
            relative to the sessions root and ``result_hash`` is the stem
            used for later retrieval via ``read_spilled``.
        """
        return _write_spilled(self._dir, session_id, content)

    def read_spilled(self, session_id: str, result_hash: str) -> str:
        """Read a previously spilled tool result.

        Args:
            session_id: Owning session.
            result_hash: Hash stem returned by ``write_spilled``.
        """
        return _read_spilled(self._dir, session_id, result_hash)
