"""Parameters for sending a user prompt."""

from __future__ import annotations

from pydantic import BaseModel

from kernel.protocol.interfaces.contracts.content_block import ContentBlock


class PromptParams(BaseModel):
    """Input to :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.prompt`."""

    session_id: str
    prompt: list[ContentBlock]
    """One or more content blocks that make up the user's message."""
    max_turns: int = 0
    """Caller-controlled limit on LLM ↔ tool iterations.  ``0`` = unlimited."""
