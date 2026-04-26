"""ConversationHistory — in-memory conversation state for one Orchestrator.

Owns the ``list[Message]`` that is passed to the LLM on each call and the
running token-count estimate used to trigger compaction.

No I/O, no subsystem dependencies.  Session persistence (JSONL) is handled
by the Session layer; this class is purely in-memory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from kernel.llm.types import (
    AssistantMessage,
    ImageContent,
    Message,  # noqa: F401  # Re-export for convenience
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolUseContent,
    UserMessage,
)
from kernel.orchestrator.types import ToolKind

# Union accepted by ``UserMessage.content``.
_UserContent = TextContent | ImageContent | ToolResultContent

logger = logging.getLogger(__name__)

# Rough token estimator used until the provider returns exact usage.
# 1 token ≈ 4 characters is a well-known heuristic that is good enough
# to decide *when* to compact; it is overwritten by UsageChunk precision.
_CHARS_PER_TOKEN = 4


@dataclass
class _ThoughtAccumulator:
    """Collects ThoughtChunk pieces for a single thinking block."""

    content_parts: list[str] = field(default_factory=list)
    signature: str = ""

    def add_content(self, text: str) -> None:
        if text:
            self.content_parts.append(text)

    def add_signature(self, sig: str) -> None:
        if sig:
            self.signature = sig

    def build(self) -> ThinkingContent | None:
        content = "".join(self.content_parts)
        if not content and not self.signature:
            return None
        return ThinkingContent(thinking=content, signature=self.signature)


class ConversationHistory:
    """In-memory conversation history with token-count tracking.

    Usage pattern (per query turn)::

        history.append_user(prompt_blocks)

        # … LLM stream …

        history.append_assistant(text, thoughts, tool_calls)
        history.update_token_count(input_tokens, output_tokens)

        # … tool execution …

        history.append_tool_results(results)

    Compaction::

        boundary = history.find_compaction_boundary(keep_recent_turns=5)
        history.replace_with_compacted(summary="…", boundary=boundary)
    """

    def __init__(self, initial_messages: list[Message] | None = None) -> None:
        self._messages: list[Message] = list(initial_messages or [])
        # Seed the estimate from any messages that were loaded from JSONL.
        self._token_count: int = self._estimate_tokens_for(self._messages)
        # tool_use_id → ToolKind mapping for compression layers (snip/microcompact).
        self._tool_kinds: dict[str, ToolKind] = {}

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    @property
    def messages(self) -> list[Message]:
        """Current message list — passed verbatim to the LLM provider."""
        return self._messages

    @property
    def token_count(self) -> int:
        """Best-known token count.  Estimate until first UsageChunk arrives."""
        return self._token_count

    # ------------------------------------------------------------------
    # Append helpers
    # ------------------------------------------------------------------

    def append_user(self, content: list[TextContent]) -> None:
        """Append a user turn (prompt blocks)."""
        wide: list[_UserContent] = list(content)
        msg = UserMessage(content=wide)
        self._messages.append(msg)
        self._token_count += self._estimate_tokens_for([msg])

    def append_assistant(
        self,
        *,
        text: str,
        thoughts: list[object],  # list[ThoughtChunk] from llm.types
        tool_calls: list[ToolUseContent],
    ) -> None:
        """Append the LLM assistant turn.

        ``thoughts`` is the raw list of ``ThoughtChunk`` objects emitted by
        the provider.  They are assembled here into ``ThinkingContent`` blocks
        so that the Anthropic API receives the required thinking/signature
        pair on the next call.

        If ``thoughts`` is empty (non-Anthropic provider or thinking disabled)
        no thinking block is added.
        """
        content: list[TextContent | ToolUseContent | ThinkingContent] = []

        # Assemble thinking blocks first (Anthropic API order: thinking before text).
        thinking = self._assemble_thinking(thoughts)
        if thinking is not None:
            content.append(thinking)

        if text:
            content.append(TextContent(text=text))

        content.extend(tool_calls)

        if not content:
            # LLM returned nothing (e.g. empty stream) — skip to avoid
            # sending an empty assistant message which some providers reject.
            logger.debug("append_assistant: empty content, skipping")
            return

        msg = AssistantMessage(content=content)
        self._messages.append(msg)
        self._token_count += self._estimate_tokens_for([msg])

    def append_tool_results(self, results: list[ToolResultContent]) -> None:
        """Append tool results as a user-role message (Anthropic style)."""
        if not results:
            return
        msg = UserMessage(content=results)  # type: ignore[arg-type]
        self._messages.append(msg)
        self._token_count += self._estimate_tokens_for([msg])

    # ------------------------------------------------------------------
    # Tool kind tracking (for compression layers 1b/1c)
    # ------------------------------------------------------------------

    def record_tool_kind(self, tool_use_id: str, kind: ToolKind) -> None:
        """Record the ToolKind for a tool_use_id.

        Called by ``ToolExecutor`` after resolving the tool, before execution.
        Used by ``Compactor.snip()`` and ``Compactor.microcompact()`` to
        identify read-only tool results.
        """
        self._tool_kinds[tool_use_id] = kind

    def tool_kind_for(self, tool_use_id: str) -> ToolKind | None:
        """Look up the ToolKind for a tool_use_id, or None if unknown."""
        return self._tool_kinds.get(tool_use_id)

    # ------------------------------------------------------------------
    # Withhold support (max_output_tokens recovery)
    # ------------------------------------------------------------------

    def pop_last_assistant(self) -> bool:
        """Remove the most-recently appended ``AssistantMessage``, if present.

        Returns ``True`` if a message was removed.  Used by the
        ``max_output_tokens`` withhold-and-retry path: STEP 4 has already
        committed the truncated assistant turn to history, but STEP 5
        decides to retry — so we undo the append before re-entering the
        loop.
        """
        if self._messages and isinstance(self._messages[-1], AssistantMessage):
            self._messages.pop()
            self._token_count = self._estimate_tokens_for(self._messages)
            return True
        return False

    # ------------------------------------------------------------------
    # Orphan tool_use detection (abort check)
    # ------------------------------------------------------------------

    def pending_tool_use_ids(self) -> list[str]:
        """Return tool_use IDs from the last assistant message that have no
        matching tool_result yet.

        Used by the Orchestrator's cancel handler to synthesise error
        results for orphan tool_use blocks (Anthropic API requires every
        tool_use to have a paired tool_result).
        """
        # Walk backwards: last assistant message may contain tool_use blocks.
        last_assistant: AssistantMessage | None = None
        last_assistant_idx = -1
        for i in range(len(self._messages) - 1, -1, -1):
            if isinstance(self._messages[i], AssistantMessage):
                last_assistant = self._messages[i]  # type: ignore[assignment]
                last_assistant_idx = i
                break

        if last_assistant is None:
            return []

        tool_use_ids = [b.id for b in last_assistant.content if isinstance(b, ToolUseContent)]
        if not tool_use_ids:
            return []

        # Collect all tool_result IDs in messages after the assistant message.
        answered: set[str] = set()
        for msg in self._messages[last_assistant_idx + 1 :]:
            for b in msg.content:
                if isinstance(b, ToolResultContent):
                    answered.add(b.tool_use_id)

        return [tid for tid in tool_use_ids if tid not in answered]

    # ------------------------------------------------------------------
    # Token count update (called after UsageChunk arrives)
    # ------------------------------------------------------------------

    def update_token_count(self, input_tokens: int, output_tokens: int) -> None:
        """Replace the running estimate with the provider's exact count.

        ``input_tokens`` is the full context size (all messages + system),
        so we use it directly as the token count for compaction threshold
        decisions.
        """
        if input_tokens > 0:
            self._token_count = input_tokens + output_tokens

    # ------------------------------------------------------------------
    # Compaction support
    # ------------------------------------------------------------------

    def find_compaction_boundary(self, keep_recent_turns: int = 5) -> int:
        """Return the index of the first message to *keep*.

        Everything before this index will be replaced by the compaction
        summary.  The boundary is chosen so that:

        1. At least ``keep_recent_turns`` complete user/assistant turn pairs
           are preserved.
        2. The boundary always falls at the start of a user message, so we
           never split an assistant+tool_result pair.
        """
        # Collect indices of user messages (turn starters).
        user_indices = [i for i, m in enumerate(self._messages) if isinstance(m, UserMessage)]

        if len(user_indices) <= keep_recent_turns:
            # Not enough turns to compact — caller should not have triggered.
            return 0

        # Keep the last `keep_recent_turns` user messages and everything after.
        keep_from_user_idx = user_indices[-(keep_recent_turns)]
        return keep_from_user_idx

    def replace_with_compacted(
        self,
        summary: str,
        boundary: int,
        summary_header: str | None = None,
    ) -> None:
        """Replace messages before ``boundary`` with a summary system message.

        Args:
            summary: The LLM-generated summary text.
            boundary: Index returned by :meth:`find_compaction_boundary`.
            summary_header: Full formatted header text.  When ``None``,
                falls back to ``"Prior conversation summary:\\n" + summary``.

        The summary is injected as a user message so that the assistant's
        context for the remaining turns includes it naturally.
        """
        kept = self._messages[boundary:]
        header_text = (
            summary_header
            if summary_header is not None
            else f"Prior conversation summary:\n{summary}"
        )
        wide: list[_UserContent] = [TextContent(text=header_text)]
        summary_msg: Message = UserMessage(content=wide)
        self._messages = [summary_msg, *kept]
        # Re-estimate after compaction.
        self._token_count = self._estimate_tokens_for(self._messages)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _assemble_thinking(
        thoughts: list[object],  # list[ThoughtChunk]
    ) -> ThinkingContent | None:
        """Merge a sequence of ThoughtChunk events into one ThinkingContent.

        The Anthropic provider emits:
        - ``ThoughtChunk(content="...", signature="")``  × N  (content deltas)
        - ``ThoughtChunk(content="", signature="abc")``  × 1  (signature)

        We join all content parts and take the non-empty signature.
        """
        if not thoughts:
            return None

        acc = _ThoughtAccumulator()
        for chunk in thoughts:
            # Access by attribute to stay decoupled from the import.
            content = getattr(chunk, "content", "")
            signature = getattr(chunk, "signature", "")
            if signature:
                acc.add_signature(signature)
            else:
                acc.add_content(content)

        return acc.build()

    @staticmethod
    def _estimate_tokens_for(messages: list[Message]) -> int:
        """Rough token estimate: count characters ÷ 4 across all messages."""
        total_chars = 0
        for msg in messages:
            for block in msg.content:
                if isinstance(block, TextContent):
                    total_chars += len(block.text)
                elif isinstance(block, ThinkingContent):
                    total_chars += len(block.thinking)
                elif isinstance(block, ToolUseContent):
                    total_chars += len(block.name) + len(str(block.input))
                elif isinstance(block, ToolResultContent):
                    c = block.content
                    if isinstance(c, str):
                        total_chars += len(c)
                    else:
                        for b in c:
                            total_chars += len(getattr(b, "text", ""))
        return max(1, total_chars // _CHARS_PER_TOKEN)
