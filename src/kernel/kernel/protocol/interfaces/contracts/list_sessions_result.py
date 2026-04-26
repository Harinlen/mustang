"""Result of listing sessions."""

from __future__ import annotations

from pydantic import BaseModel


class SessionSummary(BaseModel):
    """Lightweight metadata about a single session."""

    session_id: str
    cwd: str
    created_at: str
    """ISO-8601 UTC timestamp."""
    title: str | None = None


class ListSessionsResult(BaseModel):
    """Output from :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.list`."""

    sessions: list[SessionSummary]
    next_cursor: str | None = None
    """If present, pass this back as ``cursor`` to fetch the next page."""
