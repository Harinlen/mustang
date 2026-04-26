"""ACP session method wire-format schemas."""

from __future__ import annotations

from typing import Any

from kernel.protocol.acp.schemas.base import AcpModel
from kernel.protocol.acp.schemas.content import AcpContentBlock
from kernel.protocol.acp.schemas.enums import AcpStopReason


class AcpMcpServer(AcpModel):
    name: str
    command: str | None = None
    args: list[str] = []
    env: list[dict[str, str]] = []
    type: str | None = None
    url: str | None = None
    headers: list[dict[str, str]] = []


class AcpSessionInfo(AcpModel):
    session_id: str
    cwd: str
    created_at: str
    title: str | None = None


# session/new


class NewSessionRequest(AcpModel):
    cwd: str
    mcp_servers: list[AcpMcpServer] = []
    meta: dict[str, Any] | None = None


class NewSessionResponse(AcpModel):
    session_id: str
    config_options: list[dict] | None = None
    modes: dict | None = None
    meta: dict[str, Any] | None = None


# session/load


class LoadSessionRequest(AcpModel):
    session_id: str
    cwd: str
    mcp_servers: list[AcpMcpServer] = []
    meta: dict[str, Any] | None = None


class LoadSessionResponse(AcpModel):
    config_options: list[dict] | None = None
    modes: dict | None = None
    meta: dict[str, Any] | None = None


# session/list


class ListSessionsRequest(AcpModel):
    cursor: str | None = None
    cwd: str | None = None
    meta: dict[str, Any] | None = None


class ListSessionsResponse(AcpModel):
    sessions: list[AcpSessionInfo]
    next_cursor: str | None = None
    meta: dict[str, Any] | None = None


# session/prompt


class PromptRequest(AcpModel):
    session_id: str
    prompt: list[AcpContentBlock]
    max_turns: int = 0
    meta: dict[str, Any] | None = None


class PromptResponse(AcpModel):
    stop_reason: AcpStopReason
    meta: dict[str, Any] | None = None


# session/cancel (notification)


class CancelNotification(AcpModel):
    session_id: str
    meta: dict[str, Any] | None = None


# session/set_mode


class SetSessionModeRequest(AcpModel):
    session_id: str
    mode_id: str
    meta: dict[str, Any] | None = None


class SetSessionModeResponse(AcpModel):
    meta: dict[str, Any] | None = None


# session/set_config_option


class SetSessionConfigOptionRequest(AcpModel):
    session_id: str
    config_id: str
    value: str
    meta: dict[str, Any] | None = None


class SetSessionConfigOptionResponse(AcpModel):
    config_options: list[dict]
    meta: dict[str, Any] | None = None
