import { describe, test, expect, afterEach } from "bun:test";
import { runHeadless, type HeadlessOptions } from "./headless";
import type { AnimusConfig } from "./client/auth";
import type { ServerMessage } from "./client/protocol";

// ── Mock WebSocket Server ──

interface MockServerOptions {
  /** Called when a client message arrives. Return messages to send back. */
  onMessage?: (data: Record<string, unknown>) => ServerMessage[];
  /** Sequence of messages to send after auth_ok + tool_schemas received */
  script?: ServerMessage[];
  /** If true, never send auth_ok (simulates auth failure) */
  rejectAuth?: boolean;
  /** If true, never respond after auth (simulates hanging) */
  hang?: boolean;
}

function createMockServer(opts: MockServerOptions = {}) {
  let gotToolSchemas = false;
  let scriptSent = false;

  const server = Bun.serve({
    port: 0, // Random available port
    fetch(req, server) {
      if (req.url.endsWith("/ws/agent")) {
        const upgraded = server.upgrade(req);
        if (!upgraded) {
          return new Response("Upgrade failed", { status: 400 });
        }
        return undefined;
      }
      return new Response("Not found", { status: 404 });
    },
    websocket: {
      message(ws, data) {
        const msg = JSON.parse(String(data)) as Record<string, unknown>;

        if (msg.type === "auth") {
          if (opts.rejectAuth) {
            ws.send(
              JSON.stringify({
                type: "error",
                message: "Invalid credentials",
                code: "AUTH_FAILED",
              }),
            );
            return;
          }
          if (opts.hang) return; // Don't respond at all
          ws.send(
            JSON.stringify({
              type: "auth_ok",
              user: { id: 1, username: "test" },
            }),
          );
          return;
        }

        if (msg.type === "tool_schemas") {
          gotToolSchemas = true;
          return;
        }

        // Run script after receiving user_message
        if (msg.type === "user_message" && opts.script && !scriptSent) {
          scriptSent = true;
          for (const m of opts.script) {
            ws.send(JSON.stringify(m));
          }
          return;
        }

        // Custom handler
        if (opts.onMessage) {
          const responses = opts.onMessage(msg);
          for (const r of responses) {
            ws.send(JSON.stringify(r));
          }
        }
      },
      open() {},
      close() {},
    },
  });

  const config: AnimusConfig = {
    serverUrl: `ws://localhost:${server.port}`,
    unlockToken: "test-token",
    username: "test",
  };

  return { server, config, port: server.port };
}

// ── Tests ──

describe("headless mode", () => {
  let server: ReturnType<typeof Bun.serve> | null = null;

  afterEach(() => {
    server?.stop(true);
    server = null;
  });

  test("streams plain text output and exits 0", async () => {
    const mock = createMockServer({
      script: [
        { type: "stream_token", token: "Hello" } as ServerMessage,
        { type: "stream_token", token: " world" } as ServerMessage,
        {
          type: "turn_complete",
          response: "Hello world",
          model: "test-model",
          provider: "test",
          tools_used: [],
        } as ServerMessage,
      ],
    });
    server = mock.server;

    let exitCode = -1;
    let stdout = "";

    await runHeadless({
      config: mock.config,
      prompt: "test prompt",
      timeout: 10_000,
      exit: (code) => { exitCode = code; },
      write: (text) => { stdout += text; },
      writeError: () => {},
    });

    expect(exitCode).toBe(0);
    expect(stdout).toBe("Hello world");
  });

  test("outputs JSON when --json flag is set", async () => {
    const mock = createMockServer({
      script: [
        { type: "stream_token", token: "JSON " } as ServerMessage,
        { type: "stream_token", token: "response" } as ServerMessage,
        {
          type: "turn_complete",
          response: "JSON response",
          model: "claude-4",
          provider: "anthropic",
          tools_used: ["bash"],
        } as ServerMessage,
      ],
    });
    server = mock.server;

    let exitCode = -1;
    let stdout = "";

    await runHeadless({
      config: mock.config,
      prompt: "test",
      json: true,
      timeout: 10_000,
      exit: (code) => { exitCode = code; },
      write: (text) => { stdout += text; },
      writeError: () => {},
    });

    expect(exitCode).toBe(0);
    const parsed = JSON.parse(stdout.trim());
    expect(parsed.response).toBe("JSON response");
    expect(parsed.model).toBe("claude-4");
    expect(parsed.provider).toBe("anthropic");
    expect(parsed.tools_used).toEqual(["bash"]);
  });

  test("handles assistant_message (non-partial)", async () => {
    const mock = createMockServer({
      script: [
        {
          type: "assistant_message",
          content: "Full message",
          partial: false,
        } as ServerMessage,
        {
          type: "turn_complete",
          response: "Full message",
          model: "test",
          provider: "test",
          tools_used: [],
        } as ServerMessage,
      ],
    });
    server = mock.server;

    let stdout = "";
    let exitCode = -1;

    await runHeadless({
      config: mock.config,
      prompt: "test",
      timeout: 10_000,
      exit: (code) => { exitCode = code; },
      write: (text) => { stdout += text; },
      writeError: () => {},
    });

    expect(exitCode).toBe(0);
    expect(stdout).toContain("Full message");
  });

  test("executes safe tools and sends results back", async () => {
    let receivedToolResult = false;

    const mock = createMockServer({
      script: [
        {
          type: "tool_execute",
          tool_call_id: "tc1",
          tool_name: "bash",
          args: { command: "echo tool-output" },
        } as ServerMessage,
      ],
      onMessage: (msg) => {
        if (msg.type === "tool_result") {
          receivedToolResult = true;
          // After receiving tool result, complete the turn
          return [
            {
              type: "turn_complete",
              response: "done",
              model: "test",
              provider: "test",
              tools_used: ["bash"],
            } as ServerMessage,
          ];
        }
        return [];
      },
    });
    server = mock.server;

    let exitCode = -1;

    await runHeadless({
      config: mock.config,
      prompt: "run a command",
      timeout: 10_000,
      exit: (code) => { exitCode = code; },
      write: () => {},
      writeError: () => {},
    });

    expect(exitCode).toBe(0);
    expect(receivedToolResult).toBe(true);
  });

  test("auto-denies dangerous tools in headless mode", async () => {
    let toolResultStatus = "";

    const mock = createMockServer({
      script: [
        {
          type: "tool_execute",
          tool_call_id: "tc2",
          tool_name: "bash",
          args: { command: "rm -rf /" },
        } as ServerMessage,
      ],
      onMessage: (msg) => {
        if (msg.type === "tool_result") {
          toolResultStatus = msg.status as string;
          return [
            {
              type: "turn_complete",
              response: "",
              model: "test",
              provider: "test",
              tools_used: [],
            } as ServerMessage,
          ];
        }
        return [];
      },
    });
    server = mock.server;

    let exitCode = -1;

    await runHeadless({
      config: mock.config,
      prompt: "delete everything",
      timeout: 10_000,
      exit: (code) => { exitCode = code; },
      write: () => {},
      writeError: () => {},
    });

    expect(exitCode).toBe(0);
    expect(toolResultStatus).toBe("error");
  });

  test("exits 1 on auth failure", async () => {
    const mock = createMockServer({ rejectAuth: true });
    server = mock.server;

    let exitCode = -1;
    let stderr = "";

    await runHeadless({
      config: mock.config,
      prompt: "test",
      timeout: 10_000,
      exit: (code) => { exitCode = code; },
      write: () => {},
      writeError: (text) => { stderr += text; },
    });

    expect(exitCode).toBe(1);
    expect(stderr).toContain("Invalid credentials");
  });

  test(
    "exits 124 on timeout",
    async () => {
      const mock = createMockServer({ hang: true });
      server = mock.server;

      let exitCode = -1;
      let stderr = "";

      await runHeadless({
        config: mock.config,
        prompt: "test",
        timeout: 500, // Very short timeout
        exit: (code) => { exitCode = code; },
        write: () => {},
        writeError: (text) => { stderr += text; },
      });

      expect(exitCode).toBe(124);
      expect(stderr).toContain("Timed out");
    },
    { timeout: 10_000 },
  );
});
