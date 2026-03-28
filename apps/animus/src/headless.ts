// apps/animus/src/headless.ts
// Headless mode: connect, send prompt, stream result to stdout, exit.

import type { AnimusConfig } from "./client/auth";
import { ConnectionManager, type ConnectionStatus } from "./client/connection";
import { executeTool } from "./tools/executor";
import { ACTION_TOOL_SCHEMAS } from "./tools/registry";
import type { ServerMessage } from "./client/protocol";
import type { PermissionDecision } from "./tools/permissions";
import { setAskUserCallback, clearAskUserCallback } from "./tools/ask_user";

export interface HeadlessOptions {
  config: AnimusConfig;
  prompt: string;
  json?: boolean;
  plan?: boolean;
  timeout?: number;
  /** Injectable for testing — defaults to process.exit */
  exit?: (code: number) => void;
  /** Injectable for testing — defaults to process.stdout.write */
  write?: (text: string) => void;
  /** Injectable for testing — defaults to process.stderr.write */
  writeError?: (text: string) => void;
}

export async function runHeadless(opts: HeadlessOptions): Promise<void> {
  const {
    config,
    prompt,
    json = false,
    plan = false,
    timeout = 300_000,
    exit = (code: number) => process.exit(code),
    write = (text: string) => { process.stdout.write(text); },
    writeError = (text: string) => { process.stderr.write(text); },
  } = opts;

  let output = "";
  let reasoning = "";
  const serverToolCalls: Array<{ tool: string; args: Record<string, unknown>; result?: string }> = [];
  let promptSent = false;
  let done = false;
  let timeoutTimer: ReturnType<typeof setTimeout> | null = null;

  // In headless mode, ask_user auto-returns null (declined)
  setAskUserCallback(async () => null);

  return new Promise<void>((resolve) => {
    const finish = (conn: ConnectionManager, code: number) => {
      if (done) return;
      done = true;
      if (timeoutTimer) clearTimeout(timeoutTimer);
      clearAskUserCallback();
      conn.disconnect();
      exit(code);
      resolve();
    };

    // Headless approval: auto-deny anything requiring interactive approval
    const headlessApproval = async (): Promise<PermissionDecision> => "deny";

    const conn = new ConnectionManager(config, ACTION_TOOL_SCHEMAS, {
      onStatusChange: (status: ConnectionStatus) => {
        if (status === "connected" && !promptSent) {
          promptSent = true;
          if (plan) {
            conn.send({ type: "set_mode", mode: "plan" });
          }
          conn.send({ type: "user_message", message: prompt });
        }
      },

      onMessage: async (msg: ServerMessage) => {
        if (done) return;

        switch (msg.type) {
          case "stream_token":
            if (json) {
              output += msg.token;
            } else {
              write(msg.token);
            }
            break;

          case "assistant_message":
            if (!msg.partial) {
              if (json) {
                output += msg.content;
              } else {
                write(msg.content + "\n");
              }
            }
            break;

          case "reasoning":
            if (json) {
              reasoning += msg.content;
            } else {
              // Write reasoning to stderr so it doesn't pollute stdout piping
              writeError(`[reasoning] ${msg.content}\n`);
            }
            break;

          case "tool_call":
            // Server-side tool call — log for observability
            serverToolCalls.push({ tool: msg.tool_name, args: msg.args });
            if (!json) {
              writeError(`[server tool] ${msg.tool_name}\n`);
            }
            break;

          case "tool_return": {
            // Match to the pending server tool call
            const pending = serverToolCalls.find(
              (tc) => tc.tool === msg.tool_name && !tc.result,
            );
            if (pending) pending.result = msg.result;
            break;
          }

          case "tool_execute": {
            const result = await executeTool(msg, headlessApproval);
            conn.send({ type: "tool_result", ...result });
            break;
          }

          case "approval_required":
            // Auto-deny server-initiated approvals in headless mode
            conn.send({
              type: "approval_response",
              run_id: msg.run_id,
              tool_call_id: msg.tool_call_id,
              approved: false,
              reason: "Auto-denied in headless mode",
            });
            writeError(`[approval] Auto-denied ${msg.tool_name} in headless mode\n`);
            break;

          case "turn_complete":
            if (json) {
              const payload: Record<string, unknown> = {
                response: output,
                model: msg.model,
                provider: msg.provider,
                tools_used: msg.tools_used,
              };
              if (reasoning) payload.reasoning = reasoning;
              if (serverToolCalls.length > 0) payload.server_tool_calls = serverToolCalls;
              write(JSON.stringify(payload) + "\n");
            }
            finish(conn, 0);
            break;

          case "error":
            writeError(`Error: ${msg.message}\n`);
            // Exit on all server errors except BUSY (turn still in progress).
            // Notably AGENT_ERROR has no follow-up turn_complete, so waiting
            // would just hang until the global timeout.
            if (msg.code !== "BUSY") {
              finish(conn, 1);
            }
            break;
        }
      },

      onError: (err: Error) => {
        if (done) return;
        writeError(`Connection error: ${err.message}\n`);
        finish(conn, 1);
      },
    });

    // Overall timeout
    timeoutTimer = setTimeout(() => {
      if (done) return;
      writeError(`Timed out after ${timeout}ms\n`);
      finish(conn, 124);
    }, timeout);

    conn.connect();
  });
}
