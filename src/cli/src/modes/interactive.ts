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
import { ModelService, type ModelProfile } from "@/models/service.js";
import { SessionService } from "@/sessions/service.js";
import type { CliSessionInfo } from "@/sessions/types.js";
import { MustangAgentSessionAdapter } from "@/session/agent-session-adapter.js";
import { KeybindingsManager } from "@/active-port/coding-agent/config/keybindings.js";
import { createPromptActionAutocompleteProvider } from "@/active-port/coding-agent/modes/prompt-action-autocomplete.js";
import { getAvailableThemes, getCurrentThemeName, getEditorTheme, initTheme, setTheme, theme } from "@/active-port/coding-agent/modes/theme/theme.js";
import { copyToClipboard } from "@/active-port/coding-agent/utils/clipboard.js";
import { getSessionAccentAnsi, getSessionAccentHexForTitle } from "@/active-port/coding-agent/utils/session-color.js";
import { PermissionController } from "@/permissions/controller.js";

type ContentBlock = { type?: string; text?: string; data?: string; mimeType?: string };

type StatusLineView = {
  setMode(mode: string): void;
  setTitle(title: string): void;
  setModel(model: string): void;
  getTopBorder(width: number): { content: string; width: number };
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

type UserExecutionHandle = {
  component: Text;
  kind: "shell" | "python";
  input: string;
  output: string;
};

const StatusLineCtor = class {
  constructor(_session?: unknown) {}
  setMode(_mode: string): void {}
  setTitle(_title: string): void {}
  setModel(_model: string): void {}
  getTopBorder(_width: number): { content: string; width: number } { return { content: "", width: 0 }; }
  render(_width: number): string[] { return []; }
  invalidate(): void {}
} as unknown as new (session?: unknown) => StatusLineView;
const AssistantMessageCtor = class {
  updateContent(_message: unknown): void {}
  render(_width: number): string[] { return []; }
  invalidate(): void {}
} as unknown as new (
  message?: unknown,
  hideThinkingBlock?: boolean,
) => AssistantMessageView;
const ToolExecutionCtor = class {
  constructor(..._args: unknown[]) {}
  setExpanded(_expanded: boolean): void {}
  updateResult(..._args: unknown[]): void {}
  render(_width: number): string[] { return []; }
  invalidate(): void {}
} as unknown as new (
  toolName: string,
  args: Record<string, unknown>,
  options: Record<string, unknown>,
  tool: Record<string, unknown>,
  tui: TUI,
  cwd: string,
  toolCallId: string,
) => ToolExecutionView;

const WelcomeComponent = class {
  constructor(..._args: unknown[]) {}
  render(_width: number): string[] { return []; }
  invalidate(): void {}
};

export const BUILTIN_COMMANDS: AutocompleteItem[] = [
  { value: "help", label: "/help", description: "Show available commands" },
  { value: "model", label: "/model", description: "Show or switch model" },
  { value: "plan", label: "/plan", description: "Enter, exit, or inspect plan mode" },
  { value: "compact", label: "/compact", description: "Compact conversation context" },
  { value: "session", label: "/session", description: "List, resume, or delete sessions" },
  { value: "theme", label: "/theme", description: "Show or switch theme" },
  { value: "cost", label: "/cost", description: "Show usage and cost" },
  { value: "memory", label: "/memory", description: "List, show, or delete memories" },
  { value: "cron", label: "/cron", description: "Manage scheduled tasks" },
  { value: "auth", label: "/auth", description: "Manage secrets and auth values" },
  { value: "quit", label: "/quit", description: "Exit Mustang CLI" },
  { value: "exit", label: "/exit", description: "Exit Mustang CLI" },
];

export function sortCommandsByLabel(commands: AutocompleteItem[]): AutocompleteItem[] {
  return [...commands].sort((a, b) => {
    const aKey = a.label.replace(/^\//, "").toLowerCase();
    const bKey = b.label.replace(/^\//, "").toLowerCase();
    if (aKey < bKey) return -1;
    if (aKey > bKey) return 1;
    return 0;
  });
}

export function commandsToSlashCommands(
  commands: AutocompleteItem[],
  options: {
    modelProfiles?: ModelProfile[];
    sessionList?: CliSessionInfo[];
    themeNames?: string[];
  } = {},
): SlashCommand[] {
  return commands.map((command) => ({
    name: command.value,
    description: command.description,
    getArgumentCompletions: getArgumentCompletionFactory(command.value, options),
  }));
}

function getArgumentCompletionFactory(
  commandName: string,
  options: { modelProfiles?: ModelProfile[]; sessionList?: CliSessionInfo[]; themeNames?: string[] },
): ((argumentPrefix: string) => AutocompleteItem[] | null) | undefined {
  switch (commandName) {
    case "session":
      return (argumentPrefix) => completeSessionArguments(argumentPrefix, options.sessionList ?? []);
    case "model":
      return (argumentPrefix) => completeModelArguments(argumentPrefix, options.modelProfiles ?? []);
    case "plan":
      return (argumentPrefix) => filterCompletions(argumentPrefix, [
        { value: "enter", label: "enter", description: "Enter plan mode" },
        { value: "exit", label: "exit", description: "Exit plan mode" },
        { value: "status", label: "status", description: "Show plan mode status" },
      ]);
    case "theme":
      return (argumentPrefix) => completeThemeArguments(argumentPrefix, options.themeNames ?? []);
    default:
      return undefined;
  }
}

function completeSessionArguments(argumentPrefix: string, sessions: CliSessionInfo[]): AutocompleteItem[] | null {
  const [subcommand = "", value = ""] = argumentPrefix.split(/\s+/, 2);
  if (argumentPrefix.includes(" ") && (subcommand === "switch" || subcommand === "load")) {
    return filterCompletions(value, sessions.map((session, index) => ({
      value: session.sessionId,
      label: `${index + 1} ${session.title}`,
      description: session.cwd,
    })));
  }
  if (argumentPrefix.includes(" ") && subcommand === "delete") {
    return filterCompletions(value, [{ value: "confirm", label: "confirm", description: "Permanently delete current session" }]);
  }
  return filterCompletions(subcommand, [
    { value: "info", label: "info", description: "Show session info and stats" },
    { value: "current", label: "current", description: "Show current session" },
    { value: "list", label: "list", description: "List recent sessions" },
    { value: "new", label: "new", description: "Create and switch to a new session" },
    { value: "load", label: "load", description: "Load a session by id" },
    { value: "switch", label: "switch", description: "Switch by list number or id" },
    { value: "rename", label: "rename", description: "Rename current session" },
    { value: "archive", label: "archive", description: "Archive current session" },
    { value: "unarchive", label: "unarchive", description: "Unarchive current session" },
    { value: "delete", label: "delete", description: "Delete current session and return to selector" },
  ]);
}

function completeModelArguments(argumentPrefix: string, profiles: ModelProfile[]): AutocompleteItem[] | null {
  const [subcommand = "", value = ""] = argumentPrefix.split(/\s+/, 2);
  if (argumentPrefix.includes(" ") && (subcommand === "switch" || subcommand === "set")) {
    return filterCompletions(value, profiles.map((profile) => ({
      value: profile.name,
      label: profile.name,
      description: `${profile.providerType}/${profile.modelId}${profile.isDefault ? " (default)" : ""}`,
    })));
  }
  return filterCompletions(subcommand, [
    { value: "list", label: "list", description: "List configured model profiles" },
    { value: "switch", label: "switch", description: "Switch default model profile" },
    { value: "set", label: "set", description: "Switch default model profile" },
  ]);
}

function completeThemeArguments(argumentPrefix: string, themeNames: string[]): AutocompleteItem[] | null {
  const [subcommand = "", value = ""] = argumentPrefix.split(/\s+/, 2);
  if (argumentPrefix.includes(" ") && subcommand === "set") {
    return filterCompletions(value, themeNames.map((name) => ({ value: name, label: name })));
  }
  return filterCompletions(subcommand, [
    { value: "current", label: "current", description: "Show current theme" },
    { value: "list", label: "list", description: "List available themes" },
    { value: "set", label: "set", description: "Set theme" },
  ]);
}

function filterCompletions(prefix: string, items: AutocompleteItem[]): AutocompleteItem[] | null {
  const normalized = prefix.toLowerCase();
  const filtered = items.filter((item) => item.value.toLowerCase().startsWith(normalized));
  return filtered.length > 0 ? filtered : null;
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
  private readonly adapter: MustangAgentSessionAdapter;
  private mode: any;
  private resolveDone?: () => void;

  constructor(
    private readonly client: AcpClient,
    session: MustangSession,
    private readonly options: {
      model?: string;
      provider?: string;
      sessionService?: SessionService;
      recentSessions?: CliSessionInfo[];
      theme?: { theme: string; auto_theme: boolean; symbols: string; status_line: boolean; welcome_recent: number };
    } = {},
  ) {
    const sessionService = options.sessionService ?? new SessionService(client);
    this.adapter = new MustangAgentSessionAdapter({
      client,
      session,
      sessionService,
      recentSessions: options.recentSessions,
      defaultModel: options.model,
    });
  }

  async run(): Promise<void> {
    await this.adapter.refreshModelProfiles().catch(() => {});
    const { InteractiveMode: OmpInteractiveMode } = await importActivePortInteractiveMode();
    this.mode = new OmpInteractiveMode(this.adapter as never, "0.1.0");
    this.client.setPermissionHandler((_id, req) => {
      const controller = new PermissionController((this.mode as unknown as { ui: TUI }).ui, () => {});
      return controller.handleRequest(req);
    });
    await this.mode.init();
    void this.inputLoop();
    await new Promise<void>((resolve) => {
      this.resolveDone = resolve;
    });
  }

  private async inputLoop(): Promise<void> {
    while (true) {
      const input = await this.mode.getUserInput();
      if (input.cancelled) continue;
      try {
        if (!input.started && !this.mode.markPendingSubmissionStarted(input)) continue;
        await this.adapter.prompt(input.text, { images: input.images });
      } catch (error) {
        this.mode.showError(error instanceof Error ? error.message : String(error));
      } finally {
        this.mode.finishPendingSubmission(input);
      }
    }
  }
}

async function importActivePortInteractiveMode(): Promise<{ InteractiveMode: new (...args: any[]) => any }> {
  const load = new Function("specifier", "return import(specifier)") as (specifier: string) => Promise<unknown>;
  return await load("../active-port/coding-agent/modes/interactive-mode.ts") as { InteractiveMode: new (...args: any[]) => any };
}

class LegacyInteractiveMode {
  private readonly tui = new TUI(new ProcessTerminal(), true);
  private readonly root = new Container();
  private readonly chat = new Container();
  private readonly statusLine: StatusLineView;
  private readonly modelService: ModelService;
  private readonly permissionController: PermissionController;
  private readonly keybindings = KeybindingsManager.inMemory();
  private editor!: Editor;
  private readonly history = new MemoryHistory();
  private readonly toolExecutions = new Map<string, ToolHandle>();
  private readonly userExecutions = new Map<string, UserExecutionHandle>();
  private commands: AutocompleteItem[] = sortCommandsByLabel(BUILTIN_COMMANDS);
  private toolOutputExpanded = false;
  private currentMessage: AssistantMessageView | null = null;
  private currentText = "";
  private currentThinking = "";
  private lastSessionList: CliSessionInfo[] = [];
  private modelProfiles: ModelProfile[] = [];
  private themeNames: string[] = [];
  private modelWarning: Text | null = null;
  private busy = false;
  private isBashMode = false;
  private isPythonMode = false;
  private cancelling = false;
  private lastCtrlC = 0;
  private resolveDone?: () => void;

  constructor(
    private readonly client: AcpClient,
    private session: MustangSession,
    private readonly options: {
      model?: string;
      provider?: string;
      sessionService?: SessionService;
      recentSessions?: CliSessionInfo[];
      theme?: { theme: string; auto_theme: boolean; symbols: string; status_line: boolean; welcome_recent: number };
    } = {},
  ) {
    this.modelService = new ModelService(client);
    this.statusLine = new StatusLineCtor({
      id: session.sessionId,
      title: session.sessionId,
      agent: { model: { id: options.model ?? "" } },
    });
    this.permissionController = new PermissionController(this.tui, (message) => {
      this.chat.addChild(new Text(theme.fg("error", message), 1, 0));
      this.requestRender();
    });
  }

  async run(): Promise<void> {
    if (!this.options.theme) await initTheme(false);
    await this.refreshStartupState();
    this.installLayout();
    this.installInputHandlers();
    this.client.setPermissionHandler((_id, req) => this.permissionController.handleRequest(req));
    this.tui.start();

    await new Promise<void>((resolve) => {
      this.resolveDone = resolve;
    });
  }

  private async refreshStartupState(): Promise<void> {
    try {
      this.themeNames = await getAvailableThemes();
    } catch {
      this.themeNames = [];
    }
    try {
      const state = await this.modelService.listProfiles();
      this.modelProfiles = state.profiles;
      const defaultProfile = state.profiles.find((profile) => profile.isDefault || profile.name === state.defaultModel);
      const modelName = defaultProfile?.name ?? state.defaultModel;
      if (state.profiles.length === 0 || !modelName) {
        this.statusLine.setModel("no-model");
        this.modelWarning = new Text(
          theme.fg("warning", "Warning: No models available. Use /login or set an API key environment variable, then use /model to select a model."),
          1,
          0,
        );
      } else {
        this.statusLine.setModel(modelName);
        this.modelWarning = null;
      }
    } catch (error) {
      this.statusLine.setModel(this.options.model ?? "no-model");
      this.modelWarning = new Text(theme.fg("warning", `Warning: Could not load model profiles: ${(error as Error).message}`), 1, 0);
    }
  }

  private installLayout(): void {
    this.tui.clear();
    this.root.clear();
    this.chat.clear();

    const welcome = new WelcomeComponent(
      "0.1.0",
      this.options.model ?? "Mustang",
      this.options.provider ?? "ACP",
      (this.options.recentSessions ?? []).map(toWelcomeRecentSession),
      [],
    );

    this.editor = new Editor(getEditorTheme());
    this.editor.setHistoryStorage(this.history);
    this.editor.setPromptGutter("> ");
    this.editor.setAutocompleteProvider(this.createAutocompleteProvider());
    this.editor.onSubmit = (text) => void this.submit(text);
    this.editor.onChange = (text) => this.handleEditorChange(text);
    this.updateEditorBorderColor();
    this.syncEditorTopBorder();

    this.root.addChild(welcome);
    if (this.modelWarning) this.root.addChild(this.modelWarning);
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
          this.session.cancelExecution("any");
          this.statusLine.setMode("cancelling");
          this.chat.addChild(new Text(theme.fg("warning", "[cancelling]"), 1, 0));
          this.requestRender();
          return { consume: true };
        }
        if (now - this.lastCtrlC < 1500) {
          this.shutdown();
          return { consume: true };
        }
        this.lastCtrlC = now;
        this.chat.addChild(new Text(theme.fg("dim", "Press Ctrl+C again to exit"), 1, 0));
        this.requestRender();
        return { consume: true };
      }

      if (matchesKey(data, "ctrl+l")) {
        this.tui.invalidate();
        this.requestRender(true);
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
    if (prompt.startsWith("!")) {
      await this.submitShell(prompt);
      return;
    }
    if (prompt.startsWith("$") && !prompt.startsWith("${")) {
      await this.submitPython(prompt);
      return;
    }

    this.editor.addToHistory(prompt);
    this.chat.addChild(new Text(theme.fg("accent", `> ${prompt}`), 1, 0));
    this.chat.addChild(new Spacer(1));
    this.currentMessage = null;
    this.currentText = "";
    this.currentThinking = "";
    this.setBusy(true);
    this.requestRender();

    try {
      await this.session.prompt(prompt, () => undefined);
    } catch (error) {
      this.chat.addChild(new Text(theme.fg("error", `Error: ${(error as Error).message}`), 1, 0));
    } finally {
      this.cancelling = false;
      this.setBusy(false);
      this.currentMessage = null;
      this.currentText = "";
      this.currentThinking = "";
      this.requestRender();
    }
  }

  private handleUpdate(_update: SessionUpdateParams): void {}

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

  private async submitShell(prompt: string): Promise<void> {
    const excludeFromContext = prompt.startsWith("!!");
    const command = (excludeFromContext ? prompt.slice(2) : prompt.slice(1)).trim();
    if (!command || this.busy) return;
    this.editor.addToHistory(prompt);
    this.setBusy(true);
    this.requestRender();
    try {
      await this.session.executeShell(command, excludeFromContext, (update) => this.handleUpdate(update));
    } catch (error) {
      this.chat.addChild(new Text(theme.fg("error", `Error: ${(error as Error).message}`), 1, 0));
    } finally {
      this.cancelling = false;
      this.setBusy(false);
      this.requestRender();
    }
  }

  private async submitPython(prompt: string): Promise<void> {
    const excludeFromContext = prompt.startsWith("$$");
    const code = (excludeFromContext ? prompt.slice(2) : prompt.slice(1)).trim();
    if (!code || this.busy) return;
    this.editor.addToHistory(prompt);
    this.setBusy(true);
    this.requestRender();
    try {
      await this.session.executePython(code, excludeFromContext, (update) => this.handleUpdate(update));
    } catch (error) {
      this.chat.addChild(new Text(theme.fg("error", `Error: ${(error as Error).message}`), 1, 0));
    } finally {
      this.cancelling = false;
      this.setBusy(false);
      this.requestRender();
    }
  }

  private startUserExecution(update: SessionUpdateParams): void {
    const id = String(update.executionId ?? update.execution_id ?? "");
    if (!id) return;
    const kind = update.kind === "python" ? "python" : "shell";
    const input = String(update.input ?? "");
    const prefix = kind === "python" ? ">>>" : "$";
    const component = new Text(theme.fg(kind === "python" ? "pythonMode" : "bashMode", `${prefix} ${input}\nRunning...`), 1, 0);
    this.userExecutions.set(id, { component, kind, input, output: "" });
    this.chat.addChild(component);
    this.chat.addChild(new Spacer(1));
  }

  private updateUserExecution(update: SessionUpdateParams): void {
    const id = String(update.executionId ?? update.execution_id ?? "");
    const handle = this.userExecutions.get(id);
    if (!handle) return;
    handle.output += String(update.text ?? "");
    this.renderUserExecution(handle, "running");
  }

  private endUserExecution(update: SessionUpdateParams): void {
    const id = String(update.executionId ?? update.execution_id ?? "");
    const handle = this.userExecutions.get(id);
    if (!handle) return;
    const exitCode = Number(update.exitCode ?? update.exit_code ?? 0);
    const cancelled = Boolean(update.cancelled);
    this.renderUserExecution(handle, cancelled ? "cancelled" : exitCode === 0 ? "complete" : `exit ${exitCode}`);
  }

  private renderUserExecution(handle: UserExecutionHandle, status: string): void {
    const prefix = handle.kind === "python" ? ">>>" : "$";
    const color = handle.kind === "python" ? "pythonMode" : "bashMode";
    const body = handle.output ? `\n${theme.fg("muted", sanitizeText(handle.output).trimEnd())}` : "";
    const suffix = status === "running" ? "\nRunning..." : status === "complete" ? "" : `\n${theme.fg(status === "cancelled" ? "warning" : "error", `(${status})`)}`;
    handle.component.setText(`${theme.fg(color, `${prefix} ${handle.input}`)}${body}${suffix}`);
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
      commands: commandsToSlashCommands(this.commands, {
        modelProfiles: this.modelProfiles,
        sessionList: this.lastSessionList,
        themeNames: this.themeNames,
      }),
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
    this.requestRender();
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
        this.requestRender();
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
        this.requestRender();
        return true;
      }
      case "session":
        this.editor.addToHistory(prompt);
        void this.handleSessionCommand(args);
        return true;
      case "model":
        this.editor.addToHistory(prompt);
        void this.handleModelCommand(args);
        return true;
      case "theme":
        this.editor.addToHistory(prompt);
        void this.handleThemeCommand(args);
        return true;
      default:
        return false;
    }
  }

  private async handleSessionCommand(args: string[]): Promise<void> {
    const service = this.options.sessionService;
    if (!service) {
      this.showStatus("Session service is unavailable", "warning");
      return;
    }
    if (this.busy) {
      this.showStatus("Session command is unavailable while a prompt is running", "warning");
      return;
    }

    const subcommand = args[0] ?? "";
    try {
      switch (subcommand) {
        case "":
        case "list": {
          const archivedOnly = args.includes("--archived");
          const sessions = await service.list({ archivedOnly, includeArchived: archivedOnly, limit: 20 });
          this.lastSessionList = sessions;
          this.chat.addChild(new Text(renderSessionList(sessions), 1, 0));
          this.requestRender();
          return;
        }
        case "new": {
          const result = await service.create(process.cwd());
          this.switchSession(new MustangSession(service.clientForSession(), result.sessionId));
          this.showStatus(`Switched to new session ${result.sessionId}`, "success");
          return;
        }
        case "load": {
          const id = args[1];
          if (!id) {
            this.showStatus("Usage: /session load <session-id>", "dim");
            return;
          }
          await service.load(id, process.cwd());
          this.switchSession(new MustangSession(service.clientForSession(), id));
          this.showStatus(`Loaded session ${id}`, "success");
          return;
        }
        case "switch": {
          const target = args[1];
          if (!target) {
            this.showStatus("Usage: /session switch <number|session-id>", "dim");
            return;
          }
          const session = this.resolveSessionSwitchTarget(target);
          if (!session) {
            this.showStatus("Run /session first, then use /session switch <number>; or pass a session id", "dim");
            return;
          }
          await service.load(session.sessionId, session.cwd || process.cwd());
          this.switchSession(new MustangSession(service.clientForSession(), session.sessionId, session));
          this.showStatus(`Loaded session ${session.title}`, "success");
          return;
        }
        case "current": {
          const summary = this.session.summary;
          this.chat.addChild(new Text([
            `Session: ${this.session.sessionId}`,
            summary?.title ? `Title: ${summary.title}` : undefined,
            summary?.cwd ? `Cwd: ${summary.cwd}` : undefined,
          ].filter(Boolean).join("\n"), 1, 0));
          this.requestRender();
          return;
        }
        case "info": {
          const summary = this.session.summary;
          this.chat.addChild(new Text([
            `Session: ${this.session.sessionId}`,
            summary?.title ? `Title: ${summary.title}` : undefined,
            summary?.cwd ? `Cwd: ${summary.cwd}` : undefined,
            summary?.updatedAt ? `Updated: ${summary.updatedAt}` : undefined,
            summary?.totalInputTokens != null ? `Input tokens: ${summary.totalInputTokens}` : undefined,
            summary?.totalOutputTokens != null ? `Output tokens: ${summary.totalOutputTokens}` : undefined,
          ].filter(Boolean).join("\n"), 1, 0));
          this.requestRender();
          return;
        }
        case "rename": {
          const title = args.slice(1).join(" ").trim();
          if (!title) {
            this.showStatus("Usage: /session rename <title>", "dim");
            return;
          }
          const summary = await service.rename(this.session.sessionId, title);
          this.session.summary = summary;
          this.statusLine.setTitle(summary.title);
          this.showStatus(`Renamed session to ${summary.title}`, "success");
          return;
        }
        case "archive":
        case "unarchive": {
          const summary = await service.archive(this.session.sessionId, subcommand === "archive");
          this.session.summary = summary;
          this.showStatus(subcommand === "archive" ? "Archived current session" : "Unarchived current session", "success");
          return;
        }
        case "delete": {
          if (args[1] !== "confirm") {
            this.showStatus("Run /session delete confirm to permanently delete the current session", "warning");
            return;
          }
          await service.delete(this.session.sessionId, { force: true });
          const result = await service.create(process.cwd());
          this.switchSession(new MustangSession(service.clientForSession(), result.sessionId));
          this.showStatus(`Deleted session and switched to ${result.sessionId}`, "success");
          return;
        }
        default:
          this.showStatus("Usage: /session [list|switch|new|load|current|rename|archive|unarchive|delete]", "dim");
      }
    } catch (error) {
      this.showStatus(`Session error: ${(error as Error).message}`, "error");
    }
  }

  private resolveSessionSwitchTarget(target: string): CliSessionInfo | null {
    const index = Number(target);
    if (Number.isInteger(index) && index >= 1 && index <= this.lastSessionList.length) {
      return this.lastSessionList[index - 1];
    }
    if (Number.isInteger(index)) return null;
    return this.lastSessionList.find((session) => session.sessionId === target) ?? {
      sessionId: target,
      path: target,
      title: target,
      cwd: process.cwd(),
      updatedAt: null,
      createdAt: null,
      archivedAt: null,
      titleSource: null,
      totalInputTokens: null,
      totalOutputTokens: null,
      raw: { sessionId: target },
    };
  }

  private async handleThemeCommand(args: string[]): Promise<void> {
    const subcommand = args[0] ?? "";
    try {
      if (subcommand === "" || subcommand === "current") {
        this.showStatus(`Theme: ${getCurrentThemeName() ?? "default"}`);
        return;
      }
      if (subcommand === "list") {
        const themes = await getAvailableThemes();
        this.themeNames = themes;
        this.editor.setAutocompleteProvider(this.createAutocompleteProvider());
        this.chat.addChild(new Text(themes.join("\n"), 1, 0));
        this.requestRender();
        return;
      }
      if (subcommand === "set") {
        const name = args[1];
        if (!name) {
          this.showStatus("Usage: /theme set <name>", "dim");
          return;
        }
        await setTheme(name, false);
        (this.editor as unknown as { setTheme?: (theme: unknown) => void }).setTheme?.(getEditorTheme());
        this.showStatus(`Theme set to ${name}`, "success");
        this.tui.invalidate();
        this.requestRender(true);
        return;
      }
      this.showStatus("Usage: /theme [current|list|set <name>]", "dim");
    } catch (error) {
      this.showStatus(`Theme error: ${(error as Error).message}`, "error");
    }
  }

  private async handleModelCommand(args: string[]): Promise<void> {
    const subcommand = args[0] ?? "list";
    try {
      const state = await this.modelService.listProfiles();
      this.modelProfiles = state.profiles;
      this.editor.setAutocompleteProvider(this.createAutocompleteProvider());
      if (subcommand === "" || subcommand === "list") {
        if (state.profiles.length === 0) {
          this.showStatus("No models available. Use /login or set an API key environment variable, then use /model to select a model.", "warning");
          return;
        }
        this.chat.addChild(new Text(renderModelList(state.profiles, state.defaultModel), 1, 0));
        this.requestRender();
        return;
      }
      if (subcommand === "switch" || subcommand === "set") {
        const name = args[1];
        if (!name) {
          this.showStatus("Usage: /model switch <profile>", "dim");
          return;
        }
        const profile = state.profiles.find((entry) => entry.name === name);
        if (!profile) {
          this.showStatus(`Unknown model profile: ${name}`, "warning");
          return;
        }
        const nextDefault = await this.modelService.setDefault(profile);
        this.modelProfiles = state.profiles.map((entry) => ({ ...entry, isDefault: entry.name === profile.name }));
        this.statusLine.setModel(profile.name || nextDefault);
        this.modelWarning = null;
        this.showStatus(`Model set to ${profile.name}`, "success");
        return;
      }
      this.showStatus("Usage: /model [list|switch <profile>]", "dim");
    } catch (error) {
      this.showStatus(`Model error: ${(error as Error).message}`, "error");
    }
  }

  private switchSession(session: MustangSession): void {
    this.session = session;
    this.chat.clear();
    this.toolExecutions.clear();
    this.userExecutions.clear();
    this.currentMessage = null;
    this.currentText = "";
    this.currentThinking = "";
    this.statusLine.setTitle(session.summary?.title ?? session.sessionId);
    this.statusLine.setMode("ready");
    this.updateEditorBorderColor();
    this.chat.addChild(new Text(theme.fg("dim", `Switched session: ${session.summary?.title ?? session.sessionId}`), 1, 0));
    this.requestRender();
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
    this.updateEditorBorderColor();
    this.syncEditorTopBorder();
  }

  private handleEditorChange(text: string): void {
    const trimmed = text.trimStart();
    const wasBashMode = this.isBashMode;
    const wasPythonMode = this.isPythonMode;
    this.isBashMode = trimmed.startsWith("!");
    this.isPythonMode = trimmed.startsWith("$") && !trimmed.startsWith("${");
    if (wasBashMode !== this.isBashMode || wasPythonMode !== this.isPythonMode) {
      this.updateEditorBorderColor();
    }
  }

  private updateEditorBorderColor(): void {
    if (!this.editor) return;
    if (this.isBashMode) {
      this.editor.borderColor = theme.getBashModeBorderColor();
      return;
    }
    if (this.isPythonMode) {
      this.editor.borderColor = theme.getPythonModeBorderColor();
      return;
    }
    const summary = this.session.summary;
    const titleSource = summary?.titleSource === "auto" || summary?.titleSource === "user" ? summary.titleSource : undefined;
    const hex = getSessionAccentHexForTitle(summary?.title ?? this.session.sessionId, titleSource);
    const ansi = getSessionAccentAnsi(hex);
    this.editor.borderColor = ansi
      ? (text: string) => `${ansi}${text}\x1b[39m`
      : (text: string) => theme.fg("thinkingOff", text);
  }

  private syncEditorTopBorder(): void {
    if (!this.editor) return;
    if (this.options.theme?.status_line === false) {
      this.editor.setTopBorder(undefined);
      return;
    }
    const terminalWidth = this.tui.terminal.columns;
    this.editor.setTopBorder(this.statusLine.getTopBorder(this.editor.getTopBorderAvailableWidth(terminalWidth)));
  }

  private requestRender(force = false): void {
    this.syncEditorTopBorder();
    this.tui.requestRender(force);
  }

  private toggleToolOutputExpansion(): void {
    this.toolOutputExpanded = !this.toolOutputExpanded;
    for (const handle of this.toolExecutions.values()) {
      handle.component.setExpanded(this.toolOutputExpanded);
    }
    this.requestRender();
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

function renderSessionList(sessions: CliSessionInfo[]): string {
  if (sessions.length === 0) return "No sessions found";
  const lines = sessions.map((session, index) => {
    const archived = session.archivedAt ? " [archived]" : "";
    const cwd = session.cwd ? ` — ${session.cwd}` : "";
    return `${index + 1}. ${session.title}${archived}\n   ${session.sessionId}${cwd}`;
  });
  lines.push("", "Use /session switch <number> to switch, or /session new to start another session.");
  return lines.join("\n");
}

function renderModelList(profiles: ModelProfile[], defaultModel: string): string {
  if (profiles.length === 0) return "No models available";
  return profiles.map((profile) => {
    const marker = profile.isDefault || profile.name === defaultModel ? "* " : "  ";
    return `${marker}${profile.name}\n    ${profile.providerType}/${profile.modelId}`;
  }).join("\n");
}

function toWelcomeRecentSession(session: CliSessionInfo): { name: string; timeAgo: string } {
  return {
    name: session.title,
    timeAgo: session.updatedAt ? relativeTime(session.updatedAt) : "unknown",
  };
}

function relativeTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "unknown";
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
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
