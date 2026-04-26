"""ACP method routing tables.

``REQUEST_DISPATCH`` and ``NOTIFICATION_DISPATCH`` use **ACP schema
types** (camelCase wire format) as ``params_type`` for validation.
Handler wrappers convert ACP types -> mustang contract types before
calling the appropriate handler, keeping both the session layer and
the LLM management layer free of ACP wire-format details.

Handler targets
---------------
Each ``RequestSpec`` carries a ``target`` field that names which
kernel handler the entry routes to:

- ``"session"`` -> ``SessionHandler`` (implemented by ``SessionManager``)
- ``"model"``   -> ``ModelHandler``   (implemented by ``LLMManager``)
- ``"secrets"`` -> ``SecretManager``  (bootstrap service on module table)

``AcpSessionHandler._route_request`` reads ``target`` to select the
right handler object from ``KernelModuleTable``.  Adding a new target
is a two-step change: add the ``Literal`` value here and add the
matching ``_get_<target>_handler()`` branch in ``session_handler.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel

from kernel.llm.config import ModelRef
from kernel.protocol.acp.schemas.model import (
    AcpProfileEntry,
    AcpProviderEntry,
    AddProviderRequest,
    AddProviderResponse,
    ListProfilesRequest,
    ListProfilesResponse,
    ListProvidersRequest,
    ListProvidersResponse,
    RefreshModelsRequest,
    RefreshModelsResponse,
    RemoveProviderRequest,
    RemoveProviderResponse,
    SetDefaultModelRequest,
    SetDefaultModelResponse,
)
from kernel.protocol.acp.schemas.session import (
    AcpSessionInfo,
    CancelNotification,
    ListSessionsRequest,
    ListSessionsResponse,
    LoadSessionRequest,
    LoadSessionResponse,
    NewSessionRequest,
    NewSessionResponse,
    PromptRequest,
    PromptResponse,
    SetSessionConfigOptionRequest,
    SetSessionConfigOptionResponse,
    SetSessionModeRequest,
    SetSessionModeResponse,
)
from kernel.protocol.interfaces.contracts.add_provider_params import (
    AddProviderParams,
)
from kernel.protocol.interfaces.contracts.add_provider_result import (
    AddProviderResult,
)
from kernel.protocol.interfaces.contracts.cancel_params import CancelParams
from kernel.protocol.interfaces.contracts.handler_context import HandlerContext
from kernel.protocol.interfaces.contracts.list_profiles_params import (
    ListProfilesParams,
)
from kernel.protocol.interfaces.contracts.list_providers_params import (
    ListProvidersParams,
)
from kernel.protocol.interfaces.contracts.list_providers_result import (
    ListProvidersResult,
)
from kernel.protocol.interfaces.contracts.list_sessions_params import (
    ListSessionsParams,
)
from kernel.protocol.interfaces.contracts.list_sessions_result import (
    ListSessionsResult,
)
from kernel.protocol.interfaces.contracts.load_session_params import (
    LoadSessionParams,
)
from kernel.protocol.interfaces.contracts.load_session_result import (
    LoadSessionResult,
)
from kernel.protocol.interfaces.contracts.new_session_params import (
    NewSessionParams,
)
from kernel.protocol.interfaces.contracts.new_session_result import (
    NewSessionResult,
)
from kernel.protocol.interfaces.contracts.prompt_params import PromptParams
from kernel.protocol.interfaces.contracts.prompt_result import PromptResult
from kernel.protocol.interfaces.contracts.refresh_models_params import (
    RefreshModelsParams,
)
from kernel.protocol.interfaces.contracts.refresh_models_result import (
    RefreshModelsResult,
)
from kernel.protocol.interfaces.contracts.remove_provider_params import (
    RemoveProviderParams,
)
from kernel.protocol.interfaces.contracts.remove_provider_result import (
    RemoveProviderResult,
)
from kernel.protocol.interfaces.contracts.set_config_option_params import (
    SetConfigOptionParams,
)
from kernel.protocol.interfaces.contracts.set_config_option_result import (
    SetConfigOptionResult,
)
from kernel.protocol.interfaces.contracts.set_default_model_params import (
    SetDefaultModelParams,
)
from kernel.protocol.interfaces.contracts.set_default_model_result import (
    SetDefaultModelResult,
)
from kernel.protocol.interfaces.contracts.set_mode_params import SetModeParams
from kernel.protocol.interfaces.contracts.set_mode_result import SetModeResult
from kernel.protocol.acp.schemas.auth import AuthRequest, AuthResult
from kernel.protocol.interfaces.model_handler import ModelHandler
from kernel.protocol.interfaces.session_handler import SessionHandler

# Discriminator for which kernel subsystem handles a request.
HandlerTarget = Literal["session", "model", "secrets"]


@dataclass(frozen=True)
class RequestSpec:
    handler: Callable[[Any, HandlerContext, Any], Awaitable[BaseModel]]
    """Handler wrapper function.  First arg is the target handler object."""

    params_type: type[BaseModel]
    """ACP wire-format schema type used for validation."""

    result_type: type[BaseModel]

    target: HandlerTarget = field(default="session")
    """Which kernel handler to route this request to."""


@dataclass(frozen=True)
class NotificationSpec:
    handler: Callable[[SessionHandler, HandlerContext, Any], Awaitable[None]]
    params_type: type[BaseModel]
    """ACP wire-format schema type used for validation."""


# ---------------------------------------------------------------------------
# session/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_new(sh: SessionHandler, ctx: HandlerContext, p: NewSessionRequest) -> BaseModel:
    result = await sh.new(
        ctx,
        NewSessionParams(
            cwd=p.cwd,
            mcp_servers=[s.model_dump() for s in p.mcp_servers],
            meta=p.meta,
        ),
    )
    return NewSessionResponse(session_id=result.session_id)


async def _handle_load(sh: SessionHandler, ctx: HandlerContext, p: LoadSessionRequest) -> BaseModel:
    await sh.load_session(
        ctx,
        LoadSessionParams(
            session_id=p.session_id,
            cwd=p.cwd,
            mcp_servers=[s.model_dump() for s in p.mcp_servers],
        ),
    )
    return LoadSessionResponse()


async def _handle_list(
    sh: SessionHandler, ctx: HandlerContext, p: ListSessionsRequest
) -> BaseModel:
    result = await sh.list(ctx, ListSessionsParams(cursor=p.cursor, cwd=p.cwd))
    return ListSessionsResponse(
        sessions=[
            AcpSessionInfo(
                session_id=s.session_id,
                cwd=s.cwd,
                created_at=s.created_at,
                title=s.title,
            )
            for s in result.sessions
        ],
        next_cursor=result.next_cursor,
    )


async def _handle_prompt(sh: SessionHandler, ctx: HandlerContext, p: PromptRequest) -> BaseModel:
    from kernel.protocol.interfaces.contracts.text_block import TextBlock
    from kernel.protocol.interfaces.contracts.image_block import ImageBlock
    from kernel.protocol.interfaces.contracts.resource_block import ResourceBlock
    from kernel.protocol.interfaces.contracts.resource_link_block import ResourceLinkBlock

    _type_map = {
        "text": TextBlock,
        "image": ImageBlock,
        "resource": ResourceBlock,
        "resource_link": ResourceLinkBlock,
    }

    blocks = []
    for b in p.prompt:
        block_type = _type_map.get(b.type)  # type: ignore[union-attr]
        if block_type is not None:
            blocks.append(block_type.model_validate(b.model_dump(by_alias=False)))  # type: ignore[attr-defined]

    result = await sh.prompt(
        ctx,
        PromptParams(session_id=p.session_id, prompt=blocks, max_turns=p.max_turns),
    )
    return PromptResponse(stop_reason=result.stop_reason)


async def _handle_set_mode(
    sh: SessionHandler, ctx: HandlerContext, p: SetSessionModeRequest
) -> BaseModel:
    await sh.set_mode(ctx, SetModeParams(session_id=p.session_id, mode_id=p.mode_id))
    return SetSessionModeResponse()


async def _handle_set_config_option(
    sh: SessionHandler, ctx: HandlerContext, p: SetSessionConfigOptionRequest
) -> BaseModel:
    result = await sh.set_config_option(
        ctx,
        SetConfigOptionParams(
            session_id=p.session_id,
            config_id=p.config_id,
            value=p.value,
        ),
    )
    return SetSessionConfigOptionResponse(
        config_options=[{"configId": o.config_id, "value": o.value} for o in result.config_options]
    )


async def _handle_cancel(sh: SessionHandler, ctx: HandlerContext, p: CancelNotification) -> None:
    await sh.cancel(ctx, CancelParams(session_id=p.session_id))


# ---------------------------------------------------------------------------
# model/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_profile_list(
    mh: ModelHandler, ctx: HandlerContext, p: ListProfilesRequest
) -> BaseModel:
    result = await mh.list_profiles(ctx, ListProfilesParams())
    return ListProfilesResponse(
        profiles=[
            AcpProfileEntry(
                name=info.name,
                provider_type=info.provider_type,
                model_id=info.model_id,
                is_default=info.is_default,
            )
            for info in result.profiles
        ],
        default_model=result.default_model,
    )


async def _handle_provider_list(
    mh: ModelHandler, ctx: HandlerContext, p: ListProvidersRequest
) -> BaseModel:
    result = await mh.list_providers(ctx, ListProvidersParams())
    return ListProvidersResponse(
        providers=[
            AcpProviderEntry(
                name=info.name,
                provider_type=info.provider_type,
                models=info.models,
                roles=info.roles,
            )
            for info in result.providers
        ],
        default_model=result.default_model,
    )


async def _handle_provider_add(
    mh: ModelHandler, ctx: HandlerContext, p: AddProviderRequest
) -> BaseModel:
    result = await mh.add_provider(
        ctx,
        AddProviderParams(
            name=p.name,
            provider_type=p.provider_type,
            api_key=p.api_key,
            base_url=p.base_url,
            aws_secret_key=p.aws_secret_key,
            aws_region=p.aws_region,
            models=p.models,
        ),
    )
    return AddProviderResponse(name=result.name, models=result.models)


async def _handle_provider_remove(
    mh: ModelHandler, ctx: HandlerContext, p: RemoveProviderRequest
) -> BaseModel:
    await mh.remove_provider(ctx, RemoveProviderParams(name=p.name))
    return RemoveProviderResponse()


async def _handle_provider_refresh(
    mh: ModelHandler, ctx: HandlerContext, p: RefreshModelsRequest
) -> BaseModel:
    result = await mh.refresh_models(ctx, RefreshModelsParams(name=p.name))
    return RefreshModelsResponse(models=result.models)


async def _handle_set_default(
    mh: ModelHandler, ctx: HandlerContext, p: SetDefaultModelRequest
) -> BaseModel:
    result = await mh.set_default_model(
        ctx,
        SetDefaultModelParams(model=ModelRef(provider=p.provider, model=p.model)),
    )
    return SetDefaultModelResponse(default_model=result.default_model)


# ---------------------------------------------------------------------------
# secrets/* handler wrappers
# ---------------------------------------------------------------------------


async def _handle_auth(
    sm: Any, ctx: HandlerContext, p: Any,
) -> BaseModel:
    """Route ``secrets/auth`` actions to :class:`SecretManager`."""
    from kernel.protocol.interfaces.errors import InvalidParams
    from kernel.secrets.types import SecretNotFoundError
    import os

    action = p.action
    if action == "set":
        if not p.name or p.value is None:
            raise InvalidParams("'name' and 'value' are required for action 'set'")
        sm.set(p.name, p.value, kind=p.kind or "static")
        return AuthResult()
    if action == "get":
        if not p.name:
            raise InvalidParams("'name' is required for action 'get'")
        val = sm.get(p.name)
        return AuthResult(value=_mask_secret(val))
    if action == "list":
        return AuthResult(names=sm.list_names(kind=p.kind))
    if action == "delete":
        if not p.name:
            raise InvalidParams("'name' is required for action 'delete'")
        sm.delete(p.name)
        return AuthResult()
    if action == "import_env":
        if not p.env_var or not p.name:
            raise InvalidParams("'env_var' and 'name' are required for action 'import_env'")
        val = os.environ.get(p.env_var)
        if val is None:
            raise SecretNotFoundError(f"env var {p.env_var!r} not set")
        sm.set(p.name, val)
        return AuthResult()
    raise InvalidParams(f"Unknown auth action: {action!r}")


def _mask_secret(value: str | None) -> str | None:
    """Mask a secret value for display: show last 4 chars only."""
    if value is None:
        return None
    if len(value) <= 4:
        return "****"
    return "****" + value[-4:]


# ---------------------------------------------------------------------------
# Dispatch tables
# ---------------------------------------------------------------------------

REQUEST_DISPATCH: dict[str, RequestSpec] = {
    # session/* -- routed to SessionHandler (SessionManager)
    "session/new": RequestSpec(
        handler=_handle_new,
        params_type=NewSessionRequest,
        result_type=NewSessionResult,
        target="session",
    ),
    "session/load": RequestSpec(
        handler=_handle_load,
        params_type=LoadSessionRequest,
        result_type=LoadSessionResult,
        target="session",
    ),
    "session/list": RequestSpec(
        handler=_handle_list,
        params_type=ListSessionsRequest,
        result_type=ListSessionsResult,
        target="session",
    ),
    "session/prompt": RequestSpec(
        handler=_handle_prompt,
        params_type=PromptRequest,
        result_type=PromptResult,
        target="session",
    ),
    "session/set_mode": RequestSpec(
        handler=_handle_set_mode,
        params_type=SetSessionModeRequest,
        result_type=SetModeResult,
        target="session",
    ),
    "session/set_config_option": RequestSpec(
        handler=_handle_set_config_option,
        params_type=SetSessionConfigOptionRequest,
        result_type=SetConfigOptionResult,
        target="session",
    ),
    # model/* -- routed to ModelHandler (LLMManager)
    "model/profile_list": RequestSpec(
        handler=_handle_profile_list,
        params_type=ListProfilesRequest,
        result_type=ListProfilesResponse,
        target="model",
    ),
    "model/provider_list": RequestSpec(
        handler=_handle_provider_list,
        params_type=ListProvidersRequest,
        result_type=ListProvidersResult,
        target="model",
    ),
    "model/provider_add": RequestSpec(
        handler=_handle_provider_add,
        params_type=AddProviderRequest,
        result_type=AddProviderResult,
        target="model",
    ),
    "model/provider_remove": RequestSpec(
        handler=_handle_provider_remove,
        params_type=RemoveProviderRequest,
        result_type=RemoveProviderResult,
        target="model",
    ),
    "model/provider_refresh": RequestSpec(
        handler=_handle_provider_refresh,
        params_type=RefreshModelsRequest,
        result_type=RefreshModelsResult,
        target="model",
    ),
    "model/set_default": RequestSpec(
        handler=_handle_set_default,
        params_type=SetDefaultModelRequest,
        result_type=SetDefaultModelResult,
        target="model",
    ),
    # secrets/* -- routed to SecretManager (bootstrap service)
    "secrets/auth": RequestSpec(
        handler=_handle_auth,
        params_type=AuthRequest,
        result_type=AuthResult,
        target="secrets",
    ),
}

NOTIFICATION_DISPATCH: dict[str, NotificationSpec] = {
    "session/cancel": NotificationSpec(
        handler=_handle_cancel,
        params_type=CancelNotification,
    ),
}

OUTGOING_NOTIFICATIONS = {"session/update"}
OUTGOING_REQUESTS = {"session/request_permission"}
