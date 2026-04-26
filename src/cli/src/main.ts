/**
 * Mustang CLI — Phase A minimal REPL.
 *
 * Usage:
 *   bun run src/main.ts [--port 8200] [--session <id>]
 *
 * Env:
 *   KERNEL_URL     WebSocket URL (default: ws://localhost:8200)
 *   MUSTANG_TOKEN  Auth token (fallback: ~/.mustang/state/auth_token)
 */

import * as readline from "readline";
import chalk from "chalk";
import { AcpClient, readToken, KernelNotRunning } from "@/acp/client.js";
import { MustangSession } from "@/session.js";

// ---------------------------------------------------------------------------
// CLI args
// ---------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------
// Permission handler (Phase A: auto-allow once, print notice)
// ---------------------------------------------------------------------------

function setupPermissions(client: AcpClient): void {
  client.setPermissionHandler(async (_id, req) => {
    const tool = req.toolCall.title ?? "unknown";
    const summary = req.toolCall.inputSummary ?? "";
    process.stderr.write(
      chalk.yellow(`\n[permission] ${tool}: ${summary} → allowed\n`),
    );
    // Phase A: auto-allow once; Phase C will add interactive approval
    return { outcome: { outcome: "selected", optionId: "allow_once" } };
  });
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

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

  process.stderr.write(chalk.dim(`Connecting to ${kernelUrl}…\n`));

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

  setupPermissions(client);

  const session = loadId
    ? await MustangSession.load(client, loadId)
    : await MustangSession.create(client);

  process.stderr.write(
    chalk.dim(`Session: ${session.sessionId}\n\n`),
  );

  // ------------------------------------------------------------------
  // readline REPL
  // ------------------------------------------------------------------

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stderr, // prompt goes to stderr so stdout is clean
    terminal: true,
  });

  const prompt = () => rl.question(chalk.green("> "), handleInput);

  let busy = false;

  async function handleInput(line: string): Promise<void> {
    const text = line.trim();

    if (text === "" ) {
      prompt();
      return;
    }

    if (text === "/exit" || text === "/quit") {
      client.close();
      rl.close();
      process.exit(0);
    }

    busy = true;
    process.stdout.write("\n");

    try {
      await session.prompt(text, (update) => {
        const kind = update.sessionUpdate as string;

        if (kind === "agent_message_chunk") {
          const content = update.content as { text?: string } | undefined;
          if (content?.text) process.stdout.write(content.text);
        }
        // ignore other update types in Phase A
      });

      process.stdout.write("\n");
    } catch (e) {
      process.stderr.write(chalk.red(`\nError: ${(e as Error).message}\n`));
    }

    busy = false;
    prompt();
  }

  // Ctrl+C: cancel in-flight prompt, second Ctrl+C exits
  let ctrlCCount = 0;
  rl.on("SIGINT", () => {
    if (busy) {
      session.cancel();
      process.stderr.write(chalk.yellow("\n[cancelled]\n"));
      busy = false;
      ctrlCCount = 0;
      prompt();
      return;
    }
    ctrlCCount++;
    if (ctrlCCount >= 2) {
      client.close();
      rl.close();
      process.exit(0);
    }
    process.stderr.write(chalk.dim("\n(Press Ctrl+C again to exit)\n"));
    setTimeout(() => { ctrlCCount = 0; }, 2000);
    prompt();
  });

  prompt();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
