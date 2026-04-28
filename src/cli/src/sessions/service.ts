import { cwd as processCwd } from "node:process";
import { mapAcpSessionInfo } from "@/sessions/mapper.js";
import type {
  AcpSessionInfo,
  CliSessionInfo,
  ListSessionsOptions,
  SessionCreateResponse,
  SessionListResponse,
  SessionLoadResponse,
  SessionServiceClient,
} from "@/sessions/types.js";

export class SessionService {
  constructor(private readonly client: SessionServiceClient) {}

  clientForSession(): any {
    return this.client;
  }

  async list(options: ListSessionsOptions = {}): Promise<CliSessionInfo[]> {
    const limit = options.limit ?? 50;
    const sessions: CliSessionInfo[] = [];
    let cursor: string | null | undefined = null;

    do {
      const response = await this.client.request<SessionListResponse>("session/list", {
        cwd: options.cwd,
        includeArchived: options.includeArchived ?? false,
        archivedOnly: options.archivedOnly ?? false,
        limit: Math.max(1, Math.min(100, limit - sessions.length)),
        cursor,
      });
      for (const raw of response.sessions ?? []) {
        const mapped = mapAcpSessionInfo(raw);
        if (mapped.sessionId) sessions.push(mapped);
        if (sessions.length >= limit) return sessions;
      }
      cursor = response.nextCursor;
    } while (cursor);

    return sessions;
  }

  async create(workingDir = processCwd()): Promise<{ sessionId: string }> {
    return this.client.request<SessionCreateResponse>("session/new", {
      cwd: workingDir,
      mcpServers: [],
    });
  }

  async load(sessionId: string, workingDir = processCwd()): Promise<SessionLoadResponse> {
    return this.client.request<SessionLoadResponse>("session/load", {
      sessionId,
      cwd: workingDir,
      mcpServers: [],
    });
  }

  async rename(sessionId: string, title: string): Promise<CliSessionInfo> {
    const response = await this.client.request<AcpSessionInfo | { session: AcpSessionInfo }>("session/rename", {
      sessionId,
      title,
    });
    return mapAcpSessionInfo(unwrapSession(response));
  }

  async archive(sessionId: string, archived: boolean): Promise<CliSessionInfo> {
    const response = await this.client.request<AcpSessionInfo | { session: AcpSessionInfo }>("session/archive", {
      sessionId,
      archived,
    });
    return mapAcpSessionInfo(unwrapSession(response));
  }

  async delete(sessionId: string, options: { force?: boolean } = {}): Promise<boolean> {
    const response = await this.client.request<{ deleted?: boolean }>("session/delete", {
      sessionId,
      force: options.force ?? false,
    });
    return response.deleted ?? true;
  }
}

function unwrapSession(response: AcpSessionInfo | { session: AcpSessionInfo }): AcpSessionInfo {
  return "session" in response ? (response as { session: AcpSessionInfo }).session : response;
}
