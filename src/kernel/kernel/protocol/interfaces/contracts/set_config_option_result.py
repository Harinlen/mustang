"""Result of setting a session configuration option."""

from __future__ import annotations

from pydantic import BaseModel


class ConfigOptionValue(BaseModel):
    """A single config option with its current value."""

    config_id: str
    value: str


class SetConfigOptionResult(BaseModel):
    """Output from :meth:`~kernel.protocol.interfaces.session_handler.SessionHandler.set_config_option`.

    ACP requires the agent to return the **full** set of config options
    and their current values (not just the changed one).
    """

    config_options: list[ConfigOptionValue]
