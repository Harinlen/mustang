"""GatewayAdapter — ABC for external messaging platform integrations.

Each concrete subclass owns the full message round-trip for one platform
type (Discord, Telegram, WhatsApp, …): receive inbound message →
normalize to ``InboundMessage`` → route to session orchestrator via
``SessionManager`` → send reply back via platform API.

Design notes
------------
- ``_handle()`` is dispatched via ``asyncio.create_task()`` by platform
  event listeners — never awaited directly — so the platform's inbound
  receive loop is never blocked by a long-running LLM turn.
- A per-session ``asyncio.Lock`` in ``_session_locks`` serialises
  *session creation* for the same ``(peer_id, thread_id)`` key.  The
  lock is released before the turn runs, so permission replies from the
  user are never deadlocked by the lock the turn holds.
- Permission requests are forwarded to the platform user as a text
  message; their yes/no reply is intercepted by ``_handle()`` and
  resolves the pending ``asyncio.Future`` on ``_pending_permissions``.
"""

from __future__ import annotations

import asyncio
import orjson
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kernel.orchestrator.types import (
    PermissionCallback,
    PermissionRequest,
    PermissionResponse,
)

if TYPE_CHECKING:
    from kernel.module_table import KernelModuleTable

logger = logging.getLogger(__name__)


# Words that count as "yes" when a user replies to a permission request.
_YES_WORDS: frozenset[str] = frozenset({"yes", "y", "ok", "allow", "approve"})


# ---------------------------------------------------------------------------
# Inbound message type
# ---------------------------------------------------------------------------


@dataclass
class InboundMessage:
    """Normalised representation of a message from any platform.

    Produced by each platform's parser; consumed only within its own
    ``GatewayAdapter`` subclass.  Does not cross the
    ``Adapter → GatewayManager`` boundary.

    Attributes:
        instance_id: Config entry that received this message
            (e.g. ``"main-discord"``).
        peer_id: Platform-native user identifier.
        thread_id: Channel, thread, or group identifier used as the
            secondary session isolation key.  ``None`` for direct
            messages on platforms where the channel ID is redundant.
        text: Plain text content (stripped of platform markup).
        attachments: Platform-specific attachment payloads (images,
            files, etc.).  Not processed in the base class.
        raw: Original platform event payload, kept for debugging.
    """

    instance_id: str
    peer_id: str
    thread_id: str | None
    text: str
    attachments: list[Any] = field(default_factory=list)
    raw: Any = None


# ---------------------------------------------------------------------------
# GatewayAdapter ABC
# ---------------------------------------------------------------------------


class GatewayAdapter(ABC):
    """Communication adapter for one external messaging platform account.

    Each instance corresponds to one config entry (one bot account /
    phone number / API key).  The instance owns:

    - ``_peer_sessions`` — ``(peer_id, thread_id) → session_id`` mapping,
      persisted to disk so conversation continuity survives kernel
      restarts.
    - ``_session_locks`` — per-session ``asyncio.Lock`` guarding the
      session-creation critical section against duplicate creation.
    - ``_pending_permissions`` — per-channel futures awaiting user
      yes/no replies to tool permission requests.

    Subclasses must implement ``start()``, ``stop()``, and ``send()``.
    The message routing logic (``_handle``) and the permission callback
    factory (``_make_permission_callback``) are provided by this base
    class and should not be overridden.

    Platform event listeners in subclasses must call ``_handle`` via
    ``asyncio.create_task(self._handle(msg))`` — never ``await`` it —
    so the platform's inbound receive loop is not blocked.
    """

    def __init__(
        self,
        instance_id: str,
        config: dict[str, Any],
        module_table: KernelModuleTable,
    ) -> None:
        self._instance_id = instance_id
        self._config = config
        self._module_table = module_table

        self._peer_sessions: dict[tuple[str, str | None], str] = {}
        # Per-session lock — protects only the create_for_gateway call.
        # Must NOT be held during run_turn_for_gateway to avoid deadlock
        # with incoming permission replies.
        self._session_locks: dict[tuple[str, str | None], asyncio.Lock] = {}
        self._pending_permissions: dict[
            tuple[str, str | None], asyncio.Future[PermissionResponse]
        ] = {}

    @abstractmethod
    async def start(self) -> None:
        """Connect to the platform and begin receiving messages.

        Implementations should:
        1. Load ``_peer_sessions`` from disk (call ``_load_peer_sessions``).
        2. Establish the platform connection (outbound WS / webhook
           registration / etc.).
        """

    @abstractmethod
    async def stop(self) -> None:
        """Disconnect from the platform and release all resources.

        Implementations must call ``super().stop()`` (or inline the
        equivalent logic) to reject pending permission futures before
        tearing down the platform connection — otherwise consumer loop
        tasks will block indefinitely.
        """
        # Reject all pending permission futures so blocked turns can
        # terminate cleanly during shutdown.
        for fut in self._pending_permissions.values():
            if not fut.done():
                fut.set_result(PermissionResponse(decision="reject"))
        self._pending_permissions.clear()

    @abstractmethod
    async def send(
        self,
        peer_id: str,
        thread_id: str | None,
        text: str,
    ) -> None:
        """Deliver ``text`` back to the user via the platform's outbound API.

        Args:
            peer_id: Platform user identifier (same as in ``InboundMessage``).
            thread_id: Channel / thread identifier; ``None`` for DMs
                where the channel is implicit.
            text: Plain text reply; implementations are responsible for
                chunking to fit platform message size limits.
        """

    # ------------------------------------------------------------------
    # Inbound routing (called via asyncio.create_task by subclasses)
    # ------------------------------------------------------------------

    async def _handle(self, msg: InboundMessage) -> None:
        """Route one inbound message to the correct handler.

        Must be dispatched as ``asyncio.create_task(self._handle(msg))``
        by the platform event listener — never awaited directly.

        Routing order:
        1. If a permission reply is pending for this channel, resolve it.
        2. Acquire per-session lock and create session if needed (lock
           released before running the turn to avoid deadlock).
        3. Dispatch slash commands or run an LLM turn.
        """
        key = (msg.peer_id, msg.thread_id)

        # --- 1. Permission reply — check before anything else.
        # dict.pop is atomic in asyncio (no await between check and pop).
        # The lock must NOT be held here: the permission future is being
        # awaited inside run_turn_for_gateway which runs outside the lock;
        # if we checked inside the lock the reply task would deadlock
        # waiting for the lock the running turn holds.
        if key in self._pending_permissions:
            fut = self._pending_permissions.pop(key)
            word = msg.text.strip().lower()
            decision: str = "allow_once" if word in _YES_WORDS else "reject"
            fut.set_result(PermissionResponse(decision=decision))  # type: ignore[arg-type]
            return

        # --- 2. Session creation (serialised per key).
        from kernel.session import SessionManager

        session_manager = self._module_table.get(SessionManager)
        lock = self._session_locks.setdefault(key, asyncio.Lock())
        async with lock:
            session_id = self._peer_sessions.get(key)
            if session_id is None:
                session_id = await session_manager.create_for_gateway(
                    instance_id=self._instance_id,
                    peer_id=msg.peer_id,
                )
                self._peer_sessions[key] = session_id
                await self._persist_peer_sessions()
        # Lock released — turn execution runs outside the lock so that
        # permission reply messages can be processed concurrently.

        # --- 3. Dispatch.
        try:
            if msg.text.startswith("/"):
                await self._dispatch_command(msg, session_id)
            else:
                on_perm = self._make_permission_callback(msg.peer_id, msg.thread_id)
                reply = await session_manager.run_turn_for_gateway(session_id, msg.text, on_perm)
                if reply:  # tool-only turns produce no text
                    await self.send(msg.peer_id, msg.thread_id, reply)
        except Exception:
            logger.exception("gateway=%s peer=%s handle error", self._instance_id, msg.peer_id)
            try:
                await self.send(msg.peer_id, msg.thread_id, "An error occurred. Please try again.")
            except Exception:  # nosec B110 — best-effort; must not raise
                pass

    # ------------------------------------------------------------------
    # Command dispatch
    # ------------------------------------------------------------------

    async def _dispatch_command(self, msg: InboundMessage, session_id: str) -> None:
        """Parse a slash command and execute it via kernel internals.

        Handles ``/help`` locally.  All other commands return a short
        message directing the user to the TUI or CLI for now; the
        mapping can be expanded per-command as needed.

        Args:
            msg: The inbound message whose text starts with ``"/"``.
            session_id: Active session for this channel.
        """
        from kernel.commands import CommandManager

        name, _, _args = msg.text[1:].partition(" ")
        name = name.strip().lower()

        cmd_manager = self._module_table.get(CommandManager)
        cmd = cmd_manager.lookup(name)

        if cmd is None:
            await self.send(msg.peer_id, msg.thread_id, f"Unknown command: /{name}")
            return

        if name == "help":
            lines = ["**Available commands:**"]
            for c in cmd_manager.list_commands():
                lines.append(f"  {c.usage} — {c.description}")
            await self.send(msg.peer_id, msg.thread_id, "\n".join(lines))
            return

        # For all other commands, delegate to the typed handler.
        reply = await self._execute_for_channel(name, _args.strip(), session_id)
        await self.send(msg.peer_id, msg.thread_id, reply)

    async def _execute_for_channel(
        self,
        name: str,
        args: str,
        session_id: str,
    ) -> str:
        """Execute a slash command without a WebSocket HandlerContext.

        Calls kernel internals directly, bypassing the ACP protocol layer.

        Args:
            name: Command name (no leading slash).
            args: Raw argument string after the command name.
            session_id: Active session for this channel.

        Returns:
            Plain-text reply to send to the user.
        """
        from kernel.session import SessionManager

        session_manager = self._module_table.get(SessionManager)

        if name == "plan":
            sub = args.split()[0].lower() if args else "status"
            # Directly mutate orchestrator state — HandlerContext not needed.
            session = session_manager._get_or_raise(session_id)  # noqa: SLF001
            if sub == "enter":
                session.orchestrator.set_plan_mode(True)
                return "Plan mode enabled."
            if sub == "exit":
                session.orchestrator.set_plan_mode(False)
                return "Plan mode disabled."
            mode = "on" if session.mode_id == "plan" else "off"
            return f"Plan mode is currently **{mode}**."

        if name == "model":
            from kernel.llm import LLMManager

            llm = self._module_table.get(LLMManager)
            # _model_configs is the authoritative in-memory registry.
            model_keys = list(llm._model_configs)  # type: ignore[attr-defined]  # noqa: SLF001
            if not model_keys:
                return "No models configured."
            default_key = llm.model_for("default")
            lines = ["**Available models:**"]
            for key in model_keys:
                marker = " (default)" if key == default_key else ""
                lines.append(f"  {key}{marker}")
            return "\n".join(lines)

        if name == "session":
            sub = args.split()[0].lower() if args else "list"
            if sub == "list":
                records = await session_manager._store.list_sessions()  # noqa: SLF001
                if not records:
                    return "No sessions found."
                lines = ["**Sessions:**"]
                for r in records[:10]:
                    title = r.title or "(untitled)"
                    lines.append(f"  {r.session_id[:8]}… {title}")
                return "\n".join(lines)

        if name == "auth":
            return "/auth is only available via local ACP connection."

        # Unsupported commands — direct users to a richer client.
        return f"/{name} is not yet available in gateway context. Use the TUI or CLI."

    # ------------------------------------------------------------------
    # Permission callback factory
    # ------------------------------------------------------------------

    def _make_permission_callback(
        self,
        peer_id: str,
        thread_id: str | None,
    ) -> PermissionCallback:
        """Build an ``on_permission`` closure bound to this channel.

        When the Orchestrator requests tool approval, the closure:
        1. Sends a permission-request message to the platform user.
        2. Registers a Future on ``_pending_permissions``.
        3. Awaits the user's yes/no reply (no timeout — waits until
           the user responds or the session is cancelled).

        Args:
            peer_id: Platform user identifier.
            thread_id: Channel / thread identifier.

        Returns:
            An async callable matching ``PermissionCallback``.
        """

        async def on_permission(req: PermissionRequest) -> PermissionResponse:
            prompt = (
                f"**Permission required**: `{req.tool_name}`\n"
                f"{req.input_summary}\n"
                f"Reply **yes** to allow or **no** to deny."
            )
            await self.send(peer_id, thread_id, prompt)

            key = (peer_id, thread_id)
            fut: asyncio.Future[PermissionResponse] = asyncio.get_running_loop().create_future()
            self._pending_permissions[key] = fut

            # No timeout — wait until the user replies or the session is
            # cancelled.  Each inner layer (LLM, tool, transport) has its
            # own timeout; a blanket deadline here only mis-fires on
            # legitimate user think-time.
            try:
                return await fut
            finally:
                self._pending_permissions.pop(key, None)

        return on_permission

    # ------------------------------------------------------------------
    # Peer-session persistence
    # ------------------------------------------------------------------

    def _peer_sessions_path(self) -> Path:
        """Return the path for this adapter's peer-session mapping file."""
        return Path.home() / ".mustang" / "gateways" / self._instance_id / "peer_sessions.json"

    async def _load_peer_sessions(self) -> None:
        """Restore ``_peer_sessions`` from disk (called during ``start``)."""
        path = self._peer_sessions_path()
        if not path.exists():
            return
        try:
            raw: dict[str, str] = orjson.loads(path.read_bytes())
            # JSON keys are strings; convert back to tuple keys.
            for k, v in raw.items():
                peer_id, sep, thread_id_str = k.partition("|")
                thread_id = thread_id_str if sep else None
                self._peer_sessions[(peer_id, thread_id)] = v
            logger.info(
                "gateway=%s loaded %d peer sessions",
                self._instance_id,
                len(self._peer_sessions),
            )
        except Exception:
            logger.exception(
                "gateway=%s failed to load peer_sessions.json — starting fresh",
                self._instance_id,
            )

    async def _persist_peer_sessions(self) -> None:
        """Atomically write ``_peer_sessions`` to disk.

        Called after a new ``(peer_id, thread_id) → session_id`` mapping
        is created so the association survives kernel restarts.
        """
        path = self._peer_sessions_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Encode tuple keys as "peer_id|thread_id" strings for JSON.
        serialised = {
            f"{peer_id}|{thread_id}" if thread_id is not None else peer_id: sid
            for (peer_id, thread_id), sid in self._peer_sessions.items()
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_bytes(orjson.dumps(serialised, option=orjson.OPT_INDENT_2))
        os.replace(tmp, path)
