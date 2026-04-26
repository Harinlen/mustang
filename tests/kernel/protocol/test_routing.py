"""Tests for ACP routing handler wrappers.

Each _handle_* function converts ACP wire-format params to internal
contract types and forwards to the appropriate handler. Tests verify
the conversion and delegation logic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock


from kernel.protocol.acp.routing import (
    REQUEST_DISPATCH,
    NOTIFICATION_DISPATCH,
    _handle_new,
    _handle_load,
    _handle_list,
    _handle_set_mode,
    _handle_set_config_option,
    _handle_cancel,
    _handle_provider_list,
    _handle_provider_add,
    _handle_provider_remove,
    _handle_provider_refresh,
    _handle_set_default,
)
from kernel.protocol.acp.schemas.model import (
    AddProviderRequest,
    ListProvidersRequest,
    RefreshModelsRequest,
    RemoveProviderRequest,
    SetDefaultModelRequest,
)
from kernel.protocol.acp.schemas.session import (
    CancelNotification,
    ListSessionsRequest,
    LoadSessionRequest,
    NewSessionRequest,
    SetSessionConfigOptionRequest,
    SetSessionModeRequest,
)
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.contracts.list_providers_result import (
    ListProvidersResult,
    ProviderInfo,
)
from kernel.protocol.interfaces.contracts.new_session_result import NewSessionResult
from kernel.protocol.interfaces.contracts.load_session_result import LoadSessionResult
from kernel.protocol.interfaces.contracts.list_sessions_result import ListSessionsResult
from kernel.protocol.interfaces.contracts.set_mode_result import SetModeResult
from kernel.protocol.interfaces.contracts.set_config_option_result import (
    SetConfigOptionResult,
)
from kernel.protocol.interfaces.contracts.add_provider_result import AddProviderResult
from kernel.protocol.interfaces.contracts.remove_provider_result import RemoveProviderResult
from kernel.protocol.interfaces.contracts.refresh_models_result import RefreshModelsResult
from kernel.protocol.interfaces.contracts.set_default_model_result import (
    SetDefaultModelResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx() -> HandlerContext:
    return HandlerContext(conn=MagicMock(), sender=MagicMock(), request_id=1)


# ---------------------------------------------------------------------------
# Dispatch table structure
# ---------------------------------------------------------------------------


class TestDispatchTables:
    def test_all_session_methods_present(self) -> None:
        for method in [
            "session/new", "session/load", "session/list",
            "session/prompt", "session/set_mode", "session/set_config_option",
        ]:
            assert method in REQUEST_DISPATCH

    def test_all_model_methods_present(self) -> None:
        for method in [
            "model/provider_list", "model/provider_add",
            "model/provider_remove", "model/provider_refresh",
            "model/set_default",
        ]:
            assert method in REQUEST_DISPATCH

    def test_cancel_notification(self) -> None:
        assert "session/cancel" in NOTIFICATION_DISPATCH

    def test_session_targets(self) -> None:
        for method in ["session/new", "session/load", "session/list"]:
            assert REQUEST_DISPATCH[method].target == "session"

    def test_model_targets(self) -> None:
        for method in ["model/provider_list", "model/provider_add"]:
            assert REQUEST_DISPATCH[method].target == "model"


# ---------------------------------------------------------------------------
# Session handler wrappers
# ---------------------------------------------------------------------------


class TestHandleNew:
    async def test_delegates_to_session_handler(self) -> None:
        sh = MagicMock()
        sh.new = AsyncMock(return_value=NewSessionResult(session_id="sess-123"))
        params = NewSessionRequest(cwd="/tmp/test", mcp_servers=[])
        result = await _handle_new(sh, _ctx(), params)
        sh.new.assert_awaited_once()
        assert result.session_id == "sess-123"


class TestHandleLoad:
    async def test_delegates(self) -> None:
        sh = MagicMock()
        sh.load_session = AsyncMock(return_value=LoadSessionResult())
        params = LoadSessionRequest(session_id="sess-1", cwd="/tmp", mcp_servers=[])
        await _handle_load(sh, _ctx(), params)
        sh.load_session.assert_awaited_once()


class TestHandleList:
    async def test_converts_sessions(self) -> None:
        from kernel.protocol.interfaces.contracts.list_sessions_result import SessionSummary

        sh = MagicMock()
        sh.list = AsyncMock(
            return_value=ListSessionsResult(
                sessions=[
                    SessionSummary(
                        session_id="s1", cwd="/tmp",
                        created_at="2026-01-01T00:00:00Z", title="Test",
                    ),
                ],
                next_cursor=None,
            )
        )
        params = ListSessionsRequest()
        result = await _handle_list(sh, _ctx(), params)
        assert len(result.sessions) == 1
        assert result.sessions[0].session_id == "s1"


class TestHandleSetMode:
    async def test_delegates(self) -> None:
        sh = MagicMock()
        sh.set_mode = AsyncMock(return_value=SetModeResult())
        params = SetSessionModeRequest(session_id="s1", mode_id="plan")
        await _handle_set_mode(sh, _ctx(), params)
        sh.set_mode.assert_awaited_once()


class TestHandleSetConfigOption:
    async def test_delegates(self) -> None:
        from kernel.protocol.interfaces.contracts.set_config_option_result import ConfigOptionValue

        sh = MagicMock()
        sh.set_config_option = AsyncMock(
            return_value=SetConfigOptionResult(
                config_options=[ConfigOptionValue(config_id="thinking", value="true")]
            )
        )
        params = SetSessionConfigOptionRequest(
            session_id="s1", config_id="thinking", value="true",
        )
        result = await _handle_set_config_option(sh, _ctx(), params)
        assert len(result.config_options) == 1


class TestHandleCancel:
    async def test_delegates(self) -> None:
        sh = MagicMock()
        sh.cancel = AsyncMock()
        params = CancelNotification(session_id="s1")
        await _handle_cancel(sh, _ctx(), params)
        sh.cancel.assert_awaited_once()


# ---------------------------------------------------------------------------
# Model handler wrappers
# ---------------------------------------------------------------------------


class TestHandleProviderList:
    async def test_converts_providers(self) -> None:
        mh = MagicMock()
        mh.list_providers = AsyncMock(
            return_value=ListProvidersResult(
                providers=[
                    ProviderInfo(
                        name="anthropic",
                        provider_type="anthropic",
                        models=["claude-opus-4-6"],
                        roles={"default": True},
                    ),
                ],
                default_model=["anthropic", "claude-opus-4-6"],
            )
        )
        result = await _handle_provider_list(mh, _ctx(), ListProvidersRequest())
        assert len(result.providers) == 1
        assert result.providers[0].name == "anthropic"
        assert result.default_model == ["anthropic", "claude-opus-4-6"]


class TestHandleProviderAdd:
    async def test_delegates(self) -> None:
        mh = MagicMock()
        mh.add_provider = AsyncMock(
            return_value=AddProviderResult(name="bedrock", models=["model-a"]),
        )
        params = AddProviderRequest(
            name="bedrock",
            provider_type="bedrock",
            models=["model-a"],
        )
        result = await _handle_provider_add(mh, _ctx(), params)
        assert result.name == "bedrock"
        assert result.models == ["model-a"]


class TestHandleProviderRemove:
    async def test_delegates(self) -> None:
        mh = MagicMock()
        mh.remove_provider = AsyncMock(return_value=RemoveProviderResult())
        params = RemoveProviderRequest(name="old")
        await _handle_provider_remove(mh, _ctx(), params)
        mh.remove_provider.assert_awaited_once()


class TestHandleProviderRefresh:
    async def test_delegates(self) -> None:
        mh = MagicMock()
        mh.refresh_models = AsyncMock(
            return_value=RefreshModelsResult(models=["m1", "m2"]),
        )
        params = RefreshModelsRequest(name="anthropic")
        result = await _handle_provider_refresh(mh, _ctx(), params)
        assert result.models == ["m1", "m2"]


class TestHandleSetDefault:
    async def test_delegates(self) -> None:
        mh = MagicMock()
        mh.set_default_model = AsyncMock(
            return_value=SetDefaultModelResult(default_model=["anthropic", "sonnet"]),
        )
        params = SetDefaultModelRequest(provider="anthropic", model="sonnet")
        result = await _handle_set_default(mh, _ctx(), params)
        assert result.default_model == ["anthropic", "sonnet"]
