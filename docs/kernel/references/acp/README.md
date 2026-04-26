# ACP Reference Mirror

Local snapshot of the [Agent Client Protocol](https://agentclientprotocol.com/)
specification pages + machine-readable schemas. Mirrored so kernel design
discussions can reference ACP exactly without re-fetching.

**Do not edit these files** —— they are copies of upstream. If any file
needs updating, re-fetch from the authoritative source below.

## Source

- **Human docs**: <https://agentclientprotocol.com/>
- **Machine schema**: <https://github.com/zed-industries/agent-client-protocol/blob/main/schema/schema.json>
- **Snapshot date**: 2026-04-13
- **Protocol version at snapshot time**: `1`

## Contents

### `protocol/` — Core protocol pages

| File | Upstream | Content |
|---|---|---|
| [overview.md](protocol/overview.md) | [overview](https://agentclientprotocol.com/protocol/overview.md) | High-level flow, actor model, communication basics |
| [initialization.md](protocol/initialization.md) | [initialization](https://agentclientprotocol.com/protocol/initialization.md) | `initialize` handshake, capability negotiation, `authenticate` |
| [session-setup.md](protocol/session-setup.md) | [session-setup](https://agentclientprotocol.com/protocol/session-setup.md) | `session/new`, `session/load`, MCP servers, session id |
| [session-list.md](protocol/session-list.md) | [session-list](https://agentclientprotocol.com/protocol/session-list.md) | `session/list` (optional) |
| [session-modes.md](protocol/session-modes.md) | [session-modes](https://agentclientprotocol.com/protocol/session-modes.md) | `session/set_mode` (optional) |
| [session-config-options.md](protocol/session-config-options.md) | [session-config-options](https://agentclientprotocol.com/protocol/session-config-options.md) | `session/set_config_option` (optional) |
| [prompt-turn.md](protocol/prompt-turn.md) | [prompt-turn](https://agentclientprotocol.com/protocol/prompt-turn.md) | Core conversation loop: `session/prompt` → `session/update` stream → `PromptResponse { stopReason }` |
| [tool-calls.md](protocol/tool-calls.md) | [tool-calls](https://agentclientprotocol.com/protocol/tool-calls.md) | Tool call lifecycle, `session/request_permission`, status transitions |
| [content.md](protocol/content.md) | [content](https://agentclientprotocol.com/protocol/content.md) | `ContentBlock` variants: text / image / audio / resource_link / resource |
| [extensibility.md](protocol/extensibility.md) | [extensibility](https://agentclientprotocol.com/protocol/extensibility.md) | `_meta` field, extension methods (`<domain>/<method>`), forward compatibility |
| [schema.md](protocol/schema.md) | [schema](https://agentclientprotocol.com/protocol/schema.md) | Human-readable type reference (large, ~125 KB) |

### `rfds/` — Request For Discussion drafts

| File | Upstream | Content |
|---|---|---|
| [meta-propagation.md](rfds/meta-propagation.md) | [meta-propagation](https://agentclientprotocol.com/rfds/meta-propagation.md) | Conventions for propagating `_meta` fields across hops |
| [request-cancellation.md](rfds/request-cancellation.md) | [request-cancellation](https://agentclientprotocol.com/rfds/request-cancellation.md) | `$/cancel_request` LSP-style per-request cancellation (proposed) |

### Machine schema

- [schema.json](schema.json) —— full JSON Schema definition
  (~148 KB). Use this, not the markdown, when writing Pydantic models.

## Re-fetching

To re-pull the entire mirror:

```bash
cd docs/kernel/references/acp

# Protocol pages
for page in overview initialization session-setup session-list \
            session-modes session-config-options prompt-turn \
            tool-calls content extensibility schema; do
  curl -sL -o "protocol/${page}.md" \
    "https://agentclientprotocol.com/protocol/${page}.md"
done

# RFDs
for rfd in meta-propagation request-cancellation; do
  curl -sL -o "rfds/${rfd}.md" \
    "https://agentclientprotocol.com/rfds/${rfd}.md"
done

# JSON schema
curl -sL -o schema.json \
  https://raw.githubusercontent.com/zed-industries/agent-client-protocol/main/schema/schema.json

# Strip the documentation-site AgentInstructions wrapper
# (everything before the first `#` heading)
for f in protocol/*.md rfds/*.md; do
  sed -i -n '/^# /,$p' "$f"
done
```

## Usage in kernel design

Kernel design docs (`../../interfaces/protocol.md`, etc.) should
**link into this mirror** rather than re-stating ACP behavior, so the
kernel-side design stays focused on "how we implement ACP" rather than
"what ACP is". Example:

> See [ACP initialization handshake](references/acp/protocol/initialization.md)
> for the upstream spec.  Our transport-layer authentication happens
> before this handshake, so the `authMethods` array in our
> `InitializeResponse` is always empty.
