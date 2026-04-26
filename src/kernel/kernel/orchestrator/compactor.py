"""Compactor — context compression for ConversationHistory.

Implements STEP 1 compression layers (cheap → expensive):

- **1b snip**      — replace read-only tool results in old turns with placeholders.
- **1c microcompact** — remove entire read-only assistant+tool_result pairs.
- **1e autocompact**  — LLM-driven summarisation (last resort).

Layer 1a (tool-result budget) lives in ``ToolExecutor`` — it truncates
individual results at execution time, before they enter history.

Layer 1d (context collapse) is deferred (feature-flagged, not yet implemented).

The LLM used for summarisation is resolved via
``LLMManager.model_for_or_default("compact")`` — falls back to
``default`` (the main conversation model) when the user has not
configured a dedicated cheaper/faster compact model.
"""

from __future__ import annotations

import logging

from kernel.llm.types import (
    AssistantMessage,
    ImageContent,
    Message,
    PromptSection,
    TextContent,
    ToolResultContent,
    ToolUseContent,
    UserMessage,
)

logger = logging.getLogger(__name__)

# Number of recent turns to preserve verbatim after compaction.
_DEFAULT_KEEP_RECENT_TURNS = 5


class Compactor:
    """Compacts a ``ConversationHistory`` using the LLM provider.

    Args:
        deps: OrchestratorDeps — uses ``deps.provider`` for summarisation
            and ``deps.prompts`` for prompt text lookup.
        model: Model alias/key to use for the summarisation call.  Defaults
               to ``generic_model``; a future ``compact_model`` slot will
               allow overriding with a cheaper model.
        keep_recent_turns: Number of complete user/assistant turn pairs to
                           preserve verbatim after compaction.
    """

    def __init__(
        self,
        deps: object,  # OrchestratorDeps — avoid circular import
        model: object,  # ModelRef — avoid circular import
        keep_recent_turns: int = _DEFAULT_KEEP_RECENT_TURNS,
    ) -> None:
        self._deps = deps
        # Prefer the ``compact`` role (cheap/fast summariser); fall back
        # to the provided main-conversation model when compact is unset
        # or the provider does not expose ``model_for_or_default``.
        provider = getattr(deps, "provider", None)
        resolve = getattr(provider, "model_for_or_default", None)
        if callable(resolve):
            try:
                self._model = resolve("compact")
            except Exception:
                self._model = model
        else:
            self._model = model
        self._keep_recent = keep_recent_turns

        # Load prompt text from PromptManager (D18).
        prompts = getattr(deps, "prompts", None)
        if prompts is not None:
            self._compact_system = PromptSection(
                text=prompts.get("orchestrator/compact_system"), cache=False
            )
            self._compact_prefix = prompts.get("orchestrator/compact_prefix")
            self._compact_fallback = prompts.get("orchestrator/compact_fallback")
        else:
            # Fallback for tests that construct Compactor without PromptManager.
            self._compact_system = PromptSection(
                text=(
                    "You are a conversation summariser. "
                    "Summarise the provided conversation concisely, preserving "
                    "all key facts, decisions, file paths, code snippets, and "
                    "context needed to continue the conversation.  "
                    "Use plain text; do not add commentary."
                ),
                cache=False,
            )
            self._compact_prefix = "Summarise the following conversation:\n\n"
            self._compact_fallback = "[Conversation history compacted — summary unavailable]"

    # ------------------------------------------------------------------
    # strip_media — emergency image removal after MediaSizeError
    # ------------------------------------------------------------------

    def strip_media(self, history: object) -> int:  # history: ConversationHistory
        """Replace all ``ImageContent`` blocks in history with text placeholders.

        Called as a recovery step after ``MediaSizeError``.  Walks every
        ``UserMessage`` and replaces ``ImageContent`` blocks — both at the
        top level and inside ``ToolResultContent.content`` lists — with a
        ``TextContent`` placeholder.

        Returns the number of images stripped.
        """
        messages: list[Message] = history.messages  # type: ignore[attr-defined]
        stripped = 0

        for idx, msg in enumerate(messages):
            if not isinstance(msg, UserMessage):
                continue

            new_content: list[TextContent | ImageContent | ToolResultContent] = []
            changed = False

            for block in msg.content:
                if isinstance(block, ImageContent):
                    new_content.append(TextContent(text="[image removed — media size limit]"))
                    stripped += 1
                    changed = True
                elif isinstance(block, ToolResultContent) and isinstance(block.content, list):
                    sub_changed = False
                    new_sub: list[TextContent | ImageContent] = []
                    for sub in block.content:
                        if isinstance(sub, ImageContent):
                            new_sub.append(TextContent(text="[image removed — media size limit]"))
                            stripped += 1
                            sub_changed = True
                        else:
                            new_sub.append(sub)
                    if sub_changed:
                        block = ToolResultContent(
                            tool_use_id=block.tool_use_id,
                            content=new_sub,
                            is_error=block.is_error,
                        )
                        changed = True
                    new_content.append(block)
                else:
                    new_content.append(block)

            if changed:
                messages[idx] = UserMessage(content=new_content)

        if stripped > 0:
            # Force token-count re-estimation after stripping.
            history._token_count = history._estimate_tokens_for(messages)  # type: ignore[attr-defined]
            logger.info("Compactor: stripped %d image(s) due to media size limit", stripped)

        return stripped

    # ------------------------------------------------------------------
    # Layer 1b: Snip — replace read-only tool results with placeholders
    # ------------------------------------------------------------------

    def snip(self, history: object) -> int:  # history: ConversationHistory
        """Replace read-only tool results in non-tail messages with placeholders.

        Returns the number of characters freed.  Only affects
        ``ToolResultContent`` blocks whose corresponding tool has
        ``ToolKind.is_read_only == True``.  The ``ToolUseContent`` block
        in the preceding assistant message is preserved so the LLM still
        knows what tool was called.

        The "tail" (protected recent turns) is determined by
        ``keep_recent_turns`` — same boundary as ``compact()``.
        """
        boundary: int = history.find_compaction_boundary(  # type: ignore[attr-defined]
            keep_recent_turns=self._keep_recent
        )
        if boundary == 0:
            return 0

        freed = 0
        messages = history.messages  # type: ignore[attr-defined]
        for idx in range(boundary):
            msg = messages[idx]
            if not isinstance(msg, UserMessage):
                continue
            new_content: list[object] = []
            changed = False
            for block in msg.content:
                if isinstance(block, ToolResultContent) and not block.is_error:
                    kind = history.tool_kind_for(block.tool_use_id)  # type: ignore[attr-defined]
                    if kind is not None and kind.is_read_only:
                        old_size = _content_char_count(block.content)
                        if old_size > 0:
                            freed += old_size
                            block = ToolResultContent(
                                tool_use_id=block.tool_use_id,
                                content=f"[result snipped — {old_size} chars]",
                                is_error=False,
                            )
                            changed = True
                new_content.append(block)
            if changed:
                messages[idx] = UserMessage(content=new_content)  # type: ignore[arg-type]

        if freed > 0:
            # Rough re-estimate; exact count comes from next UsageChunk.
            history._token_count = history._estimate_tokens_for(messages)  # type: ignore[attr-defined]
            logger.info("Compactor: snipped %d chars from read-only tool results", freed)
        return freed

    # ------------------------------------------------------------------
    # Layer 1c: Microcompact — remove entire read-only tool pairs
    # ------------------------------------------------------------------

    def microcompact(self, history: object) -> int:  # history: ConversationHistory
        """Remove entire read-only assistant + tool_result pairs from non-tail.

        Returns the number of message pairs removed.  A "read-only pair"
        is an ``AssistantMessage`` that contains **only** ``ToolUseContent``
        blocks (no ``TextContent``) where every tool is ``is_read_only``,
        followed by a ``UserMessage`` containing only ``ToolResultContent``
        blocks.
        """
        boundary: int = history.find_compaction_boundary(  # type: ignore[attr-defined]
            keep_recent_turns=self._keep_recent
        )
        if boundary <= 1:
            return 0

        messages = history.messages  # type: ignore[attr-defined]
        indices_to_remove: set[int] = set()
        i = 0
        while i < boundary - 1:
            msg = messages[i]
            next_msg = messages[i + 1]
            if (
                isinstance(msg, AssistantMessage)
                and _is_read_only_assistant(msg, history)
                and isinstance(next_msg, UserMessage)
                and _is_tool_result_only(next_msg)
            ):
                indices_to_remove.add(i)
                indices_to_remove.add(i + 1)
                i += 2
            else:
                i += 1

        if not indices_to_remove:
            return 0

        n_pairs = len(indices_to_remove) // 2
        marker = UserMessage(
            content=[TextContent(text=f"[{n_pairs} read-only tool calls removed]")]
        )

        # Rebuild messages: keep non-removed pre-boundary + marker + tail.
        kept_pre = [m for j, m in enumerate(messages[:boundary]) if j not in indices_to_remove]
        # Insert marker at the position of the first removed pair.
        insert_pos = min(indices_to_remove)
        kept_pre.insert(min(insert_pos, len(kept_pre)), marker)
        history._messages = kept_pre + messages[boundary:]  # type: ignore[attr-defined]
        history._token_count = history._estimate_tokens_for(history._messages)  # type: ignore[attr-defined]
        logger.info("Compactor: microcompacted %d read-only tool pairs", n_pairs)
        return n_pairs

    # ------------------------------------------------------------------
    # Layer 1e: Autocompact — LLM-driven summarisation
    # ------------------------------------------------------------------

    async def compact(self, history: object) -> None:  # history: ConversationHistory
        """Compact ``history`` in-place.

        Finds the compaction boundary, summarises everything before it,
        and calls ``history.replace_with_compacted()``.

        No-op if the boundary is 0 (not enough history to compact).
        """
        # Import here to avoid circular dependency at module level.

        boundary: int = history.find_compaction_boundary(  # type: ignore[attr-defined]
            keep_recent_turns=self._keep_recent
        )
        if boundary == 0:
            logger.debug("Compactor: boundary=0, nothing to compact")
            return

        messages_to_summarise: list[Message] = history.messages[:boundary]  # type: ignore[attr-defined]

        if not messages_to_summarise:
            return

        summary = await self._summarise(messages_to_summarise)

        # Format the summary header via PromptManager when available.
        prompts = getattr(self._deps, "prompts", None)
        summary_header: str | None = None
        if prompts is not None:
            summary_header = prompts.render("orchestrator/summary_header", summary=summary)

        history.replace_with_compacted(  # type: ignore[attr-defined]
            summary=summary, boundary=boundary, summary_header=summary_header
        )
        logger.info(
            "Compactor: compacted %d messages into summary (%d chars)",
            boundary,
            len(summary),
        )

    async def _summarise(self, messages: list[Message]) -> str:
        """Call the LLM to produce a plain-text summary of ``messages``."""
        from kernel.llm.types import StreamError, TextChunk

        provider = self._deps.provider  # type: ignore[attr-defined]

        # Build a single user message that contains the conversation text.
        conversation_text = _render_messages(messages)
        summarise_request = [
            UserMessage(content=[TextContent(text=self._compact_prefix + conversation_text)])
        ]

        parts: list[str] = []
        try:
            async for chunk in await provider.stream(
                system=[self._compact_system],
                messages=summarise_request,
                tool_schemas=[],
                model=self._model,
                temperature=None,
            ):
                if isinstance(chunk, TextChunk):
                    parts.append(chunk.content)
                elif isinstance(chunk, StreamError):
                    logger.warning(
                        "Compactor: stream error during summarisation: %s", chunk.message
                    )
                    break
        except Exception as exc:
            # Any provider error during summarisation is non-fatal: use a
            # placeholder so the orchestrator can continue rather than crash.
            logger.warning("Compactor: provider error during summarisation: %s", exc)

        summary = "".join(parts).strip()
        if not summary:
            summary = self._compact_fallback
        return summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _content_char_count(content: str | list[TextContent | ImageContent]) -> int:
    """Count characters in a ToolResultContent.content value."""
    if isinstance(content, str):
        return len(content)
    total = 0
    for block in content:
        total += len(getattr(block, "text", ""))
    return total


def _is_read_only_assistant(msg: AssistantMessage, history: object) -> bool:
    """True if every content block is a ToolUseContent with is_read_only kind."""
    if not msg.content:
        return False
    for block in msg.content:
        if not isinstance(block, ToolUseContent):
            return False
        kind = history.tool_kind_for(block.id)  # type: ignore[attr-defined]
        if kind is None or not kind.is_read_only:
            return False
    return True


def _is_tool_result_only(msg: UserMessage) -> bool:
    """True if the message contains only ToolResultContent blocks."""
    if not msg.content:
        return False
    return all(isinstance(b, ToolResultContent) for b in msg.content)


def _render_messages(messages: list[Message]) -> str:
    """Render a message list as plain text for the summarisation prompt."""
    from kernel.llm.types import (
        TextContent,
        ThinkingContent,
        ToolResultContent,
        ToolUseContent,
        UserMessage,
    )

    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            role = "User"
        else:
            role = "Assistant"

        parts: list[str] = []
        for block in msg.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
            elif isinstance(block, ThinkingContent):
                parts.append(f"[thinking: {block.thinking[:200]}…]")
            elif isinstance(block, ToolUseContent):
                parts.append(f"[tool_use: {block.name}({block.input})]")
            elif isinstance(block, ToolResultContent):
                content = block.content
                text = content if isinstance(content, str) else str(content)[:200]
                parts.append(f"[tool_result: {text}]")

        if parts:
            lines.append(f"{role}: {'  '.join(parts)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Skill compaction preservation
# ---------------------------------------------------------------------------

# Per-skill token budget for compaction preservation.
_POST_COMPACT_MAX_TOKENS_PER_SKILL = 5_000
_POST_COMPACT_SKILLS_TOKEN_BUDGET = 25_000
_CHARS_PER_TOKEN = 4  # rough estimate
_SKILL_TRUNCATION_MARKER = (
    "\n\n[... skill content truncated for compaction; "
    "use Read on the skill path if you need the full text]"
)


def create_skill_attachment(
    skills: object,  # SkillManager
    agent_id: str | None = None,
) -> str | None:
    """Build a text attachment of invoked skill bodies for post-compact
    re-injection.

    Aligned with Claude Code's ``createSkillAttachmentIfNeeded``:

    - Skills sorted most-recent-first (by ``invoked_at``).
    - Each skill body truncated to ~5000 tokens (head preserved —
      setup/usage instructions are typically at the top).
    - Total budget ~25000 tokens.
    - Returns ``None`` if no skills are invoked.

    Note: the **skill listing** (catalog) is NOT re-injected after
    compaction (aligned with Claude Code — the LLM still has the
    Skill tool schema and invoked skill content).
    """
    if skills is None:
        return None

    try:
        invoked = skills.get_invoked_for_agent(agent_id)  # type: ignore[attr-defined,union-attr]
    except Exception:
        return None

    if not invoked:
        return None

    per_skill_char_budget = _POST_COMPACT_MAX_TOKENS_PER_SKILL * _CHARS_PER_TOKEN
    total_char_budget = _POST_COMPACT_SKILLS_TOKEN_BUDGET * _CHARS_PER_TOKEN

    sections: list[str] = []
    used_chars = 0

    for info in invoked:
        content = info.content
        # Per-skill truncation.
        if len(content) > per_skill_char_budget:
            content = content[:per_skill_char_budget] + _SKILL_TRUNCATION_MARKER

        # Total budget check.
        if used_chars + len(content) > total_char_budget:
            break

        sections.append(
            f'<skill name="{info.skill_name}" path="{info.skill_path}">\n{content}\n</skill>'
        )
        used_chars += len(content)

    if not sections:
        return None

    return (
        "The following skills were previously invoked in this session. "
        "Their instructions remain active:\n\n" + "\n\n".join(sections)
    )
