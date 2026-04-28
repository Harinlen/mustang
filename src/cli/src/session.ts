import { AcpClient, ExecutionResult, PromptResult, SessionUpdateParams } from "@/acp/client.js";
import { cwd } from "process";

export class MustangSession {
  constructor(
    private client: AcpClient,
    public readonly sessionId: string,
  ) {}

  static async create(
    client: AcpClient,
    workingDir?: string,
  ): Promise<MustangSession> {
    const result = await client.request<{ sessionId: string }>("session/new", {
      cwd: workingDir ?? cwd(),
      mcpServers: [],
    });
    return new MustangSession(client, result.sessionId);
  }

  static async load(
    client: AcpClient,
    id: string,
    workingDir?: string,
  ): Promise<MustangSession> {
    await client.request("session/load", {
      sessionId: id,
      cwd: workingDir ?? cwd(),
      mcpServers: [],
    });
    return new MustangSession(client, id);
  }

  async prompt(
    text: string,
    onUpdate: (update: SessionUpdateParams) => void,
  ): Promise<PromptResult> {
    const unsub = this.client.onUpdate(onUpdate);
    try {
      return await this.client.promptRequest(this.sessionId, text);
    } finally {
      unsub();
    }
  }

  async executeShell(
    command: string,
    excludeFromContext: boolean,
    onUpdate: (update: SessionUpdateParams) => void,
  ): Promise<ExecutionResult> {
    const unsub = this.client.onUpdate(onUpdate);
    try {
      return await this.client.executeShellRequest(this.sessionId, command, excludeFromContext);
    } finally {
      unsub();
    }
  }

  async executePython(
    code: string,
    excludeFromContext: boolean,
    onUpdate: (update: SessionUpdateParams) => void,
  ): Promise<ExecutionResult> {
    const unsub = this.client.onUpdate(onUpdate);
    try {
      return await this.client.executePythonRequest(this.sessionId, code, excludeFromContext);
    } finally {
      unsub();
    }
  }

  cancel(): void {
    this.client.notify("session/cancel", { sessionId: this.sessionId });
  }

  cancelExecution(kind: "shell" | "python" | "any" = "any"): void {
    this.client.notify("session/cancel_execution", { sessionId: this.sessionId, kind });
  }

  async setMode(mode: "default" | "plan"): Promise<void> {
    await this.client.request("session/set_mode", {
      sessionId: this.sessionId,
      modeId: mode,
    });
  }
}
