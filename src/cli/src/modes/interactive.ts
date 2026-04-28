import {
  Container,
  Editor,
  ProcessTerminal,
  Spacer,
  Text,
  TUI,
  type AutocompleteItem,
  type AutocompleteProvider,
  type SlashCommand,
  matchesKey,
} from "@/tui/index.js";
import { sanitizeText } from "@/compat/pi-natives.js";
import type { AcpClient, SessionUpdateParams } from "@/acp/client.js";
import { MustangSession } from "@/session.js";
import { AssistantMessageComponent } from "@/active-port/coding-agent/modes/components/assistant-message.js";
import { StatusLineComponent } from "@/active-port/coding-agent/modes/components/status-line.js";
import { ToolExecutionComponent } from "@/active-port/coding-agent/modes/components/tool-execution.js";
import { WelcomeComponent } from "@/active-port/coding-agent/modes/components/welcome.js";
import { KeybindingsManager } from "@/active-port/coding-agent/config/keybindings.js";
import { createPromptActionAutocompleteProvider } from "@/active-port/coding-agent/modes/prompt-action-autocomplete.js";
import { getEditorTheme, initTheme, theme } from "@/active-port/coding-agent/modes/theme/theme.js";
import { copyToClipboard } from "@/active-port/coding-agent/utils/clipboard.js";
import { PermissionController } from "@/permissions/controller.js";

type ContentBlock = { type?: string; text?: string; data?: string; mimeType?: string };

type StatusLineView = {
  setMode(mode: string): void;
  setTitle(title: string): void;
  render(width: number): string[];
  invalidate(): void;
};

type AssistantMessageView = {
  updateContent(message: { role: "assistant"; content: Array<{ type: "thinking"; thinking: string } | { type: "text"; text: string }> }): void;
  render(width: number): string[];
  invalidate(): void;
};

type ToolExecutionView = {
  setExpanded(expanded: boolean): void;
  updateResult(
    result: {
      content: Array<{ type: string; text?: string; data?: string; mimeType?: string }>;
      details?: { locations?: unknown };
      isError?: boolean;
    },
    isPartial?: boolean,
    toolCallId?: string,
  ): void;
  render(width: number): string[];
  invalidate(): void;
};

type ToolHandle = {
  component: ToolExecutionView;
  title: string;
};

const StatusLineCtor = StatusLineComponent as unknown as new (session?: unknown) => StatusLineView;
const AssistantMessageCtor = AssistantMessageComponent as unknown as new (
  message?: unknown,
  hideThinkingBlock?: boolean,
) => AssistantMessageView;
const ToolExecutionCtor = ToolExecutionComponent as unknown as new (
  toolName: string,
  args: Record<string, unknown>,
  options: Record<string, unknown>,
  tool: Record<string, unknown>,
  tui: TUI,
  cwd: string,
  toolCallId: string,
) => ToolExecutionView;

const BUILTIN_COMMANDS: AutocompleteItem[] = [
  { value: "help", label: "/help", description: "Show available commands" },
  { value: "model", label: "/model", description: "Show or switch model" },
  { value: "plan", label: "/plan", description: "Enter, exit, or inspect plan mode" },
  { value: "compact", label: "/compact", description: "Compact conversation context" },
  { value: "session", label: "/session", description: "List, resume, or delete sessions" },
  { value: "cost", label: "/cost", description: "Show usage and cost" },
  { value: "memory", label: "/memory", description: "List, show, or delete memories" },
  { value: "cron", label: "/cron", description: "Manage scheduled tasks" },
  { value: "auth", label: "/auth", description: "Manage secrets and auth values" },
  { value: "quit", label: "/quit", description: "Exit Mustang CLI" },
  { value: "exit", label: "/exit", description: "Exit Mustang CLI" },
];

function sortCommandsByLabel(commands: AutocompleteItem[]): AutocompleteItem[] {
  return [...commands].sort((a, b) => {
    const aKey = a.label.replace(/^\//, "").toLowerCase();
    const bKey = b.label.replace(/^\//, "").toLowerCase();
    if (aKey < bKey) return -1;
    if (aKey > bKey) return 1;
    return 0;
  });
}

function commandsToSlashCommands(commands: AutocompleteItem[]): SlashCommand[] {
  return commands.map((command) => ({
    name: command.value,
    description: command.description,
  }));
}

class MemoryHistory {
  #items: Array<{ prompt: string }> = [];

  async add(prompt: string): Promise<void> {
    if (!prompt.trim()) return;
    if (this.#items[0]?.prompt === prompt) return;
    this.#items.unshift({ prompt });
    this.#items = this.#items.slice(0, 100);
  }

  getRecent(limit: number): Array<{ prompt: string }> {
    return this.#items.slice(0, limit);
  }
}

export class InteractiveMode {
  private readonly tui = new TUI(new ProcessTerminal(), true);
  private readonly root = new Container();
  private readonly chat = new Container();
  private readonly statusLine: StatusLineView;
  private readonly permissionController: PermissionController;
  private readonly keybindings = KeybindingsManager.inMemory();
  private editor!: Editor;
  private readonly history = new MemoryHistory();
  private readonly toolExecutions = new Map<string, ToolHandle>();
  private commands: AutocompleteItem[] = sortCommandsByLabel(BUILTIN_COMMANDS);
  private toolOutputExpanded = false;
  private currentMessage: AssistantMessageView | null = null;
  private currentText = "";
  private currentThinking = "";
  private busy = false;
  private cancelling = false;
  private lastCtrlC = 0;
  private resolveDone?: () => void;

  constructor(
    private readonly client: AcpClient,
    private readonly session: MustangSession,
    private readonly options: { model?: string; provider?: string } = {},
  ) {
    this.statusLine = new StatusLineCtor({
      id: session.sessionId,
      title: session.sessionId,
      agent: { model: { id: options.model ?? "" } },
    });
    this.permissionController = new PermissionController(this.tui, (message) => {
      this.chat.addChild(new Text(theme.fg("error", message), 1, 0));
      this.tui.requestRender();
    });
  }

  async run(): Promise<void> {
    await initTheme(false);
    this.installLayout();
    this.installInputHandlers();
    this.client.setPermissionHandler((_id, req) => this.permissionController.handleRequest(req));
    this.tui.start();

    await new Promise<void>((resolve) => {
      this.resolveDone = resolve;
    });
  }

  private installLayout(): void {
    this.tui.clear();
    this.root.clear();
    this.chat.clear();

    const welcome = new WelcomeComponent(
      "0.1.0",
      this.options.model ?? "Mustang",
      this.options.provider ?? "ACP",
      [],
      [],
    );

    this.editor = new Editor(getEditorTheme());
    this.editor.setHistoryStorage(this.history);
    this.editor.setPromptGutter("> ");
    this.editor.setAutocompleteProvider(this.createAutocompleteProvider());
    this.editor.onSubmit = (text) => void this.submit(text);

    this.root.addChild(welcome);
    this.root.addChild(this.chat);
    this.root.addChild(new Spacer(1));
    this.root.addChild(this.statusLine);
    this.root.addChild(this.editor);
    this.tui.addChild(this.root);
    this.tui.setFocus(this.editor);
  }

  private installInputHandlers(): void {
    this.tui.addInputListener((data) => {
      if (matchesKey(data, "ctrl+c")) {
        if (this.tui.hasOverlay()) return undefined;
        const now = Date.now();
        if (this.busy) {
          if (this.cancelling) return { consume: true };
          this.cancelling = true;
          this.session.cancel();
          this.statusLine.setMode("cancelling");
          this.chat.addChild(new Text(theme.fg("warning", "[cancelling]"), 1, 0));
          this.tui.requestRender();
          return { consume: true };
        }
        if (now - this.lastCtrlC < 1500) {
          this.shutdown();
          return { consume: true };
        }
        this.lastCtrlC = now;
        this.chat.addChild(new Text(theme.fg("dim", "Press Ctrl+C again to exit"), 1, 0));
        this.tui.requestRender();
        return { consume: true };
      }

      if (matchesKey(data, "ctrl+l")) {
        this.tui.invalidate();
        this.tui.requestRender(true);
        return { consume: true };
      }

      if (isCtrlO(data)) {
        this.toggleToolOutputExpansion();
        return { consume: true };
      }

      return undefined;
    });

    process.once("SIGTERM", () => this.shutdown());
    process.once("SIGHUP", () => this.shutdown());
  }

  private async submit(text: string): Promise<void> {
    const prompt = text.trim();
    if (!prompt || this.busy) return;

    if (this.handleLocalSlashCommand(prompt)) return;

    this.editor.addToHistory(prompt);
    this.chat.addChild(new Text(theme.fg("accent", `> ${prompt}`), 1, 0));
    this.chat.addChild(new Spacer(1));
    this.currentMessage = null;
    this.currentText = "";
    this.currentThinking = "";
    this.setBusy(true);
    this.tui.requestRender();

    try {
      await this.session.prompt(prompt, (update) => this.handleUpdate(update));
    } catch (error) {
      this.chat.addChild(new Text(theme.fg("error", `Error: ${(error as Error).message}`), 1, 0));
    } finally {
      this.cancelling = false;
      this.setBusy(false);
      this.currentMessage = null;
      this.currentText = "";
      this.currentThinking = "";
      this.tui.requestRender();
    }
  }

  private handleUpdate(update: SessionUpdateParams): void {
    switch (update.sessionUpdate) {
      case "agent_message_chunk":
        this.appendAssistantText(extractText(update.content));
        break;
      case "agent_thought_chunk":
        this.appendAssistantThinking(extractText(update.content));
        break;
      case "tool_call":
        this.startTool(update);
        break;
      case "tool_call_update":
        this.updateTool(update);
        break;
      case "current_mode_update":
        this.statusLine.setMode(String(update.modeId ?? "default"));
        break;
      case "session_info_update":
        if (update.title) this.statusLine.setTitle(String(update.title));
        break;
      case "available_commands_update":
        this.updateCommands(update.availableCommands ?? update.available_commands);
        break;
    }
    this.tui.requestRender();
  }

  private appendAssistantText(text: string): void {
    if (!text) return;
    this.currentText += text;
    this.ensureAssistantMessage();
    this.renderAssistantMessage();
  }

  private appendAssistantThinking(text: string): void {
    if (!text) return;
    this.currentThinking += text;
    this.ensureAssistantMessage();
    this.renderAssistantMessage();
  }

  private ensureAssistantMessage(): void {
    if (this.currentMessage) return;
    this.currentMessage = new AssistantMessageCtor(undefined, false);
    this.chat.addChild(this.currentMessage);
  }

  private renderAssistantMessage(): void {
    const content = [];
    if (this.currentThinking) content.push({ type: "thinking" as const, thinking: this.currentThinking });
    if (this.currentText) content.push({ type: "text" as const, text: this.currentText });
    this.currentMessage?.updateContent({ role: "assistant", content });
  }

  private startTool(update: SessionUpdateParams): void {
    const id = String(update.toolCallId ?? update.tool_call_id ?? "");
    if (!id) return;
    const title = String(update.title ?? "tool");
    const rawInput = typeof update.rawInput === "string" ? update.rawInput : typeof update.raw_input === "string" ? update.raw_input : "";
    const args = parseJsonObject(rawInput) ?? {};
    const component = new ToolExecutionCtor(
      title,
      args,
      {},
      { name: title, label: title, status: String(update.status ?? "pending") },
      this.tui,
      process.cwd(),
      id,
    );
    component.setExpanded(this.toolOutputExpanded);
    this.toolExecutions.set(id, { component, title });
    this.chat.addChild(component);
  }

  private updateTool(update: SessionUpdateParams): void {
    const id = String(update.toolCallId ?? update.tool_call_id ?? "");
    const handle = this.toolExecutions.get(id);
    if (!handle) return;
    const status = String(update.status ?? "");
    const content = normalizeToolContent(update.content, status);
    handle.component.updateResult(
      {
        content,
        isError: status === "failed" || status === "error",
        details: update.locations ? { locations: update.locations } : undefined,
      },
      status === "in_progress",
      id,
    );
  }

  private updateCommands(raw: unknown): void {
    if (!Array.isArray(raw)) return;
    const remoteCommands = raw.map((entry) => {
      const command = entry as Record<string, unknown>;
      const rawName = String(command.name ?? command.command ?? "");
      const name = rawName.startsWith("/") ? rawName.slice(1) : rawName;
      return {
        value: name,
        label: `/${name}`,
        description: command.description ? String(command.description) : undefined,
      };
    }).filter((item) => item.value !== "");
    const merged = new Map<string, AutocompleteItem>();
    for (const item of BUILTIN_COMMANDS) merged.set(item.value, item);
    for (const item of remoteCommands) merged.set(item.value, item);
    this.commands = sortCommandsByLabel([...merged.values()]);
    this.editor.setAutocompleteProvider(this.createAutocompleteProvider());
  }

  private createAutocompleteProvider(): AutocompleteProvider {
    return createPromptActionAutocompleteProvider({
      commands: commandsToSlashCommands(this.commands),
      basePath: process.cwd(),
      keybindings: this.keybindings,
      copyCurrentLine: () => this.copyCurrentLine(),
      copyPrompt: () => this.copyPrompt(),
      undo: (prefix) => this.editor.undoPastTransientText(prefix),
      moveCursorToMessageEnd: () => this.editor.moveToMessageEnd(),
      moveCursorToMessageStart: () => this.editor.moveToMessageStart(),
      moveCursorToLineStart: () => this.editor.moveToLineStart(),
      moveCursorToLineEnd: () => this.editor.moveToLineEnd(),
    });
  }

  private showStatus(message: string, color: "dim" | "success" | "warning" | "error" = "dim"): void {
    this.chat.addChild(new Text(theme.fg(color, message), 1, 0));
    this.tui.requestRender();
  }

  private copyCurrentLine(): void {
    const { line } = this.editor.getCursor();
    const text = this.editor.getLines()[line] || "";
    if (!text) {
      this.showStatus("Nothing to copy");
      return;
    }
    void copyToClipboard(text).then(
      () => this.showStatus(`Copied line: ${previewText(text)}`, "success"),
      () => this.showStatus("Failed to copy to clipboard", "warning"),
    );
  }

  private copyPrompt(): void {
    const text = this.editor.getText();
    if (!text) {
      this.showStatus("Nothing to copy");
      return;
    }
    void copyToClipboard(text).then(
      () => this.showStatus(`Copied: ${previewText(text)}`, "success"),
      () => this.showStatus("Failed to copy to clipboard", "warning"),
    );
  }

  private handleLocalSlashCommand(prompt: string): boolean {
    const [command, ...args] = prompt.slice(1).split(/\s+/);
    if (!prompt.startsWith("/") || !command) return false;

    switch (command) {
      case "help":
        this.editor.addToHistory(prompt);
        this.chat.addChild(new Text(this.renderHelp(), 1, 0));
        this.tui.requestRender();
        return true;
      case "quit":
      case "exit":
        this.shutdown();
        return true;
      case "plan": {
        const subcommand = args[0];
        if (subcommand === "enter") {
          void this.session.setMode("plan");
          this.statusLine.setMode("plan");
          this.chat.addChild(new Text(theme.fg("success", "Entered plan mode"), 1, 0));
        } else if (subcommand === "exit") {
          void this.session.setMode("default");
          this.statusLine.setMode("ready");
          this.chat.addChild(new Text(theme.fg("success", "Exited plan mode"), 1, 0));
        } else {
          this.chat.addChild(new Text(theme.fg("dim", "Usage: /plan enter | /plan exit"), 1, 0));
        }
        this.editor.addToHistory(prompt);
        this.tui.requestRender();
        return true;
      }
      default:
        return false;
    }
  }

  private renderHelp(): string {
    const lines = ["Available commands", ""];
    for (const command of this.commands) {
      lines.push(`${command.label.padEnd(12)}${command.description ?? ""}`);
    }
    return lines.join("\n");
  }

  private setBusy(busy: boolean): void {
    this.busy = busy;
    this.editor.disableSubmit = busy;
    this.statusLine.setMode(busy ? "running" : "ready");
  }

  private toggleToolOutputExpansion(): void {
    this.toolOutputExpanded = !this.toolOutputExpanded;
    for (const handle of this.toolExecutions.values()) {
      handle.component.setExpanded(this.toolOutputExpanded);
    }
    this.tui.requestRender();
  }

  private shutdown(): void {
    this.client.close();
    this.tui.stop();
    this.resolveDone?.();
  }
}

function previewText(text: string): string {
  const sanitized = sanitizeText(text).replace(/\s+/g, " ").trim();
  return sanitized.length > 30 ? `${sanitized.slice(0, 30)}...` : sanitized;
}

function extractText(content: unknown): string {
  const block = content as ContentBlock | undefined;
  if (!block) return "";
  if (typeof block.text === "string") return block.text;
  return "";
}

function normalizeToolContent(content: unknown, status: string): Array<{ type: string; text?: string; data?: string; mimeType?: string }> {
  if (Array.isArray(content)) {
    return content.map((block) => {
      const item = block as ContentBlock;
      return {
        type: item.type ?? "text",
        text: item.text,
        data: item.data,
        mimeType: item.mimeType,
      };
    });
  }
  if (typeof content === "string") return [{ type: "text", text: content }];
  if (status === "in_progress") return [{ type: "text", text: "Running..." }];
  if (status) return [{ type: "text", text: status }];
  return [];
}

function parseJsonObject(value: string): Record<string, unknown> | null {
  if (!value.trim()) return null;
  try {
    const parsed = JSON.parse(value) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : null;
  } catch {
    return null;
  }
}

function isCtrlO(data: string): boolean {
  return data === "\x0f" || matchesKey(data, "ctrl+o");
}
