/**
 * Mustang CLI — ACP TUI client.
 *
 * Usage:
 *   bun run src/main.ts [--port 8200] [--session <id>]
 *
 * Env:
 *   KERNEL_URL     WebSocket URL (default: ws://localhost:8200)
 *   MUSTANG_TOKEN  Auth token (fallback: ~/.mustang/state/auth_token)
 */

import chalk from "chalk";
import { AcpClient, KernelNotRunning, readToken } from "@/acp/client.js";
import { InteractiveMode } from "@/modes/interactive.js";
import { MustangSession } from "@/session.js";

function parseArgs(): { port: number; sessionId: string | null } {
  const args = process.argv.slice(2);
  let port = 8200;
  let sessionId: string | null = null;

  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--port" && args[i + 1]) {
      port = parseInt(args[++i], 10);
    } else if (args[i] === "--session" && args[i + 1]) {
      sessionId = args[++i];
    }
  }

  return { port, sessionId };
}

async function main(): Promise<void> {
  const { port, sessionId: loadId } = parseArgs();
  const kernelUrl = process.env.KERNEL_URL ?? `ws://localhost:${port}`;

  let token: string;
  try {
    token = readToken();
  } catch (e) {
    console.error(chalk.red((e as Error).message));
    process.exit(1);
  }

  let client: AcpClient;
  try {
    client = await AcpClient.connect(kernelUrl, token);
  } catch (e) {
    if (e instanceof KernelNotRunning) {
      console.error(chalk.red(e.message));
    } else {
      console.error(chalk.red(`Connection failed: ${(e as Error).message}`));
    }
    process.exit(1);
  }

  const session = loadId
    ? await MustangSession.load(client, loadId)
    : await MustangSession.create(client);

  await new InteractiveMode(client, session).run();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
