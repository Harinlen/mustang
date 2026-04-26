import { AcpClient, PromptResult, SessionUpdateParams } from "@/acp/client.js";
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

  cancel(): void {
    this.client.notify("session/cancel", { sessionId: this.sessionId });
  }

  async setMode(mode: "default" | "plan"): Promise<void> {
    await this.client.request("session/set_mode", {
      sessionId: this.sessionId,
      modeId: mode,
    });
  }
}
