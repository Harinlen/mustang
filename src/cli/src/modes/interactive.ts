import {
  CombinedAutocompleteProvider,
  Container,
  Editor,
  ProcessTerminal,
  Spacer,
  Text,
  TUI,
  type AutocompleteItem,
  matchesKey,
} from "@/tui/index.js";
import type { AcpClient, SessionUpdateParams } from "@/acp/client.js";
import { MustangSession } from "@/session.js";
import { AssistantMessageComponent } from "@/active-port/coding-agent/modes/components/assistant-message.js";
import { StatusLineComponent } from "@/active-port/coding-agent/modes/components/status-line.js";
import { ToolExecutionComponent } from "@/active-port/coding-agent/modes/components/tool-execution.js";
import { WelcomeComponent } from "@/active-port/coding-agent/modes/components/welcome.js";
import { getEditorTheme, initTheme, theme } from "@/active-port/coding-agent/modes/theme/theme.js";

type ContentBlock = { type?: string; text?: string; data?: string; mimeType?: string };

type ToolHandle = {
  component: ToolExecutionComponent;
  title: string;
};

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
  private readonly statusLine: StatusLineComponent;
  private editor!: Editor;
  private readonly history = new MemoryHistory();
  private readonly toolExecutions = new Map<string, ToolHandle>();
  private commands: AutocompleteItem[] = [];
  private currentMessage: AssistantMessageComponent | null = null;
  private currentText = "";
  private currentThinking = "";
  private busy = false;
  private lastCtrlC = 0;
  private resolveDone?: () => void;

  constructor(
    private readonly client: AcpClient,
    private readonly session: MustangSession,
    private readonly options: { model?: string; provider?: string } = {},
  ) {
    this.statusLine = new StatusLineComponent({
      id: session.sessionId,
      title: session.sessionId,
      agent: { model: { id: options.model ?? "" } },
    });
  }

  async run(): Promise<void> {
    await initTheme(false);
    this.installLayout();
    this.installInputHandlers();
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
    this.editor.setAutocompleteProvider(new CombinedAutocompleteProvider(this.commands));
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
        const now = Date.now();
        if (this.busy) {
          this.session.cancel();
          this.setBusy(false);
          this.chat.addChild(new Text(theme.fg("warning", "[cancelled]"), 1, 0));
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

      return undefined;
    });

    process.once("SIGTERM", () => this.shutdown());
    process.once("SIGHUP", () => this.shutdown());
  }

  private async submit(text: string): Promise<void> {
    const prompt = text.trim();
    if (!prompt || this.busy) return;

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
    this.currentMessage = new AssistantMessageComponent(undefined, false);
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
    const component = new ToolExecutionComponent(
      title,
      args,
      {},
      { name: title, label: title, status: String(update.status ?? "pending") },
      this.tui,
      process.cwd(),
      id,
    );
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
    this.commands = raw.map((entry) => {
      const command = entry as Record<string, unknown>;
      const name = String(command.name ?? command.command ?? "");
      return {
        value: name.startsWith("/") ? name : `/${name}`,
        label: name.startsWith("/") ? name : `/${name}`,
        description: command.description ? String(command.description) : undefined,
      };
    }).filter((item) => item.value !== "/");
    this.editor.setAutocompleteProvider(new CombinedAutocompleteProvider(this.commands));
  }

  private setBusy(busy: boolean): void {
    this.busy = busy;
    this.editor.disableSubmit = busy;
    this.statusLine.setMode(busy ? "running" : "ready");
  }

  private shutdown(): void {
    this.client.close();
    this.tui.stop();
    this.resolveDone?.();
  }
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
