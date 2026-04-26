"""Result of creating a new session."""

from __future__ import annotations

from pydantic import BaseModel


class NewSessionResult(BaseModel):
    """Output from :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.new`."""

    session_id: str
    """Unique identifier for the created session."""
