/**
 * ACP WebSocket client — JSON-RPC 2.0 over WebSocket.
 *
 * Protocol quirks:
 * - Auth via URL query param: ?token=xxx or ?password=xxx
 * - Must send `initialize` after connect before any session/* calls
 * - session/prompt response arrives BEFORE streaming session/update chunks
 * - session/request_permission is a kernel-initiated request; we reply with
 *   a JSON-RPC response (not a notification)
 */

import WebSocket from "ws";
import { readFileSync } from "fs";
import { homedir } from "os";
import { join } from "path";

// ---------------------------------------------------------------------------
// Wire types (camelCase, matches kernel ACP schema)
// ---------------------------------------------------------------------------

export interface SessionUpdateParams {
  sessionUpdate: string;
  sessionId: string;
  [key: string]: unknown;
}

export interface PermissionRequest {
  reqId: number;
  sessionId: string;
  toolCall: {
    toolCallId: string;
    title?: string;
    inputSummary?: string;
  };
  options: Array<{ optionId: string; name: string; kind: string }>;
  toolInput?: Record<string, unknown>;
}

export interface PermissionResult {
  outcome: {
    outcome: "selected";
    optionId: string;
    updatedInput?: Record<string, unknown>;
  };
}

export interface PromptResult {
  stopReason: string;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

export class AcpError extends Error {
  constructor(
    public code: number,
    message: string,
  ) {
    super(`[${code}] ${message}`);
    this.name = "AcpError";
  }
}

export class KernelNotRunning extends Error {
  constructor(url: string) {
    super(`Cannot connect to kernel at ${url}. Is the kernel running?`);
    this.name = "KernelNotRunning";
  }
}

// ---------------------------------------------------------------------------
// AcpClient
// ---------------------------------------------------------------------------

type UpdateHandler = (params: SessionUpdateParams) => void;
type PermissionHandler = (
  id: number,
  req: PermissionRequest,
) => Promise<PermissionResult>;

export class AcpClient {
  private reqId = 0;
  private pending = new Map<
    number,
    { resolve: (v: unknown) => void; reject: (e: Error) => void }
  >();
  private updateHandlers = new Set<UpdateHandler>();
  private permissionHandler?: PermissionHandler;

  private constructor(private ws: WebSocket) {
    ws.on("message", (raw) => {
      try {
        this.handleIncoming(JSON.parse(raw.toString()));
      } catch (e) {
        console.error("[acp] failed to parse frame:", e);
      }
    });
  }

  // ------------------------------------------------------------------
  // Connection
  // ------------------------------------------------------------------

  static async connect(url: string, token: string): Promise<AcpClient> {
    const base = url.replace(/\/$/, "");
    const wsUrl = `${base}/session?token=${encodeURIComponent(token)}`;
    const ws = new WebSocket(wsUrl);

    // Permanent error sink — prevents unhandled error events after once() fires.
    ws.on("error", () => {});

    try {
      await new Promise<void>((resolve, reject) => {
        ws.once("open", resolve);
        ws.once("error", () => reject(new KernelNotRunning(url)));
      });
    } catch (e) {
      ws.terminate();
      throw e;
    }

    const client = new AcpClient(ws);

    // Must initialize before any session/* calls
    await client.request("initialize", {
      protocolVersion: 1,
      clientCapabilities: {},
      clientInfo: { name: "mustang-cli", version: "0.1.0" },
    });

    return client;
  }

  close(): void {
    this.ws.close();
  }

  // ------------------------------------------------------------------
  // Inbound routing
  // ------------------------------------------------------------------

  private handleIncoming(msg: Record<string, unknown>): void {
    if ("id" in msg && ("result" in msg || "error" in msg)) {
      // JSON-RPC response to one of our requests
      this.routeResponse(msg);
    } else if (msg.method === "session/update") {
      const params = msg.params as { sessionId: string; update: SessionUpdateParams };
      for (const h of this.updateHandlers) h(params.update);
    } else if (msg.method === "session/request_permission") {
      // Kernel-initiated request — must reply with a response
      this.handlePermission(msg);
    }
  }

  private routeResponse(msg: Record<string, unknown>): void {
    const id = msg.id as number;
    const entry = this.pending.get(id);
    if (!entry) return;
    this.pending.delete(id);

    if ("error" in msg) {
      const err = msg.error as { code: number; message: string };
      entry.reject(new AcpError(err.code, err.message));
    } else {
      entry.resolve(msg.result);
    }
  }

  private async handlePermission(msg: Record<string, unknown>): Promise<void> {
    const id = msg.id as number;
    const params = msg.params as {
      sessionId: string;
      toolCall: PermissionRequest["toolCall"];
      options: PermissionRequest["options"];
      toolInput?: Record<string, unknown>;
    };

    const req: PermissionRequest = {
      reqId: id,
      sessionId: params.sessionId,
      toolCall: params.toolCall,
      options: params.options,
      toolInput: params.toolInput,
    };

    let result: PermissionResult;
    if (this.permissionHandler) {
      try {
        result = await this.permissionHandler(id, req);
      } catch {
        // On error, deny
        result = {
          outcome: { outcome: "selected", optionId: "deny" },
        };
      }
    } else {
      // Default: allow once
      result = {
        outcome: { outcome: "selected", optionId: "allow_once" },
      };
    }

    this.respond(id, result);
  }

  // ------------------------------------------------------------------
  // Outbound helpers
  // ------------------------------------------------------------------

  private nextId(): number {
    return ++this.reqId;
  }

  private send(msg: unknown): void {
    this.ws.send(JSON.stringify(msg));
  }

  /** Send a JSON-RPC response to a kernel-initiated request. */
  respond(id: number, result: unknown): void {
    this.send({ jsonrpc: "2.0", id, result });
  }

  /** Send a request and await the response. Rejects on JSON-RPC error. */
  async request<R = unknown>(
    method: string,
    params: unknown,
    opts: { timeoutMs?: number } = {},
  ): Promise<R> {
    const id = this.nextId();
    const timeoutMs = opts.timeoutMs ?? 30_000;

    return new Promise<R>((resolve, reject) => {
      let timer: ReturnType<typeof setTimeout> | undefined;

      this.pending.set(id, {
        resolve: (v) => {
          clearTimeout(timer);
          resolve(v as R);
        },
        reject: (e) => {
          clearTimeout(timer);
          reject(e);
        },
      });

      if (timeoutMs > 0) {
        timer = setTimeout(() => {
          this.pending.delete(id);
          reject(
            new Error(
              `Kernel did not respond to ${method} (id=${id}) within ${timeoutMs}ms`,
            ),
          );
        }, timeoutMs);
      }

      this.send({ jsonrpc: "2.0", id, method, params });
    });
  }

  /**
   * Send session/prompt and wait for the response.
   * Uses no timeout (turns include unbounded user interaction).
   * Adds a 50ms drain after the response arrives so trailing
   * session/update chunks can fire their handlers before we return.
   */
  async promptRequest(sessionId: string, text: string): Promise<PromptResult> {
    const result = await this.request<PromptResult>(
      "session/prompt",
      {
        sessionId,
        prompt: [{ type: "text", text }],
      },
      { timeoutMs: 0 }, // no timeout
    );
    // Kernel sends response before trailing session/update chunks
    await new Promise((r) => setTimeout(r, 50));
    return result;
  }

  /** Send a notification (no response expected). */
  notify(method: string, params: unknown): void {
    this.send({ jsonrpc: "2.0", method, params });
  }

  // ------------------------------------------------------------------
  // Event subscription
  // ------------------------------------------------------------------

  /** Subscribe to session/update notifications. Returns an unsubscribe fn. */
  onUpdate(handler: UpdateHandler): () => void {
    this.updateHandlers.add(handler);
    return () => this.updateHandlers.delete(handler);
  }

  /** Set the global handler for session/request_permission requests. */
  setPermissionHandler(handler: PermissionHandler): void {
    this.permissionHandler = handler;
  }
}

// ---------------------------------------------------------------------------
// Token helpers
// ---------------------------------------------------------------------------

const TOKEN_PATH = join(homedir(), ".mustang", "state", "auth_token");

export function readToken(): string {
  const envToken = process.env.MUSTANG_TOKEN;
  if (envToken) return envToken;

  try {
    return readFileSync(TOKEN_PATH, "utf-8").trim();
  } catch {
    throw new Error(
      `No auth token found. Set MUSTANG_TOKEN or run the kernel first (token at ${TOKEN_PATH}).`,
    );
  }
}
