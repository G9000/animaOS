import { describe, expect, test } from "bun:test";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import {
  buildTargetSpec,
  buildTargetEnvironment,
  createDevSessionContinuity,
  createServerReloadScheduler,
  getServerRestartCandidate,
  startDevStack,
} from "../scripts/dev-root-lib.mjs";

type ChildStub = {
  readonly pid: number;
  readonly killed: boolean;
  kill: () => void;
  once: (event: string, handler: (...args: unknown[]) => void) => void;
  emit: (event: string, ...args: unknown[]) => void;
};

function makeChild(pid: number): ChildStub {
  let killed = false;
  const listeners = new Map<string, (...args: unknown[]) => void>();
  return {
    pid,
    get killed() {
      return killed;
    },
    kill() {
      killed = true;
    },
    once(event, handler) {
      listeners.set(event, handler);
    },
    emit(event, ...args) {
      listeners.get(event)?.(...args);
    },
  };
}

describe("startDevStack", () => {
  test("uses a direct server command that root bun dev can restart itself", () => {
    const repoRoot = "C:\\repo";
    const spec = buildTargetSpec("server", repoRoot, "node.exe");

    expect(spec.command).toBe("uv");
    expect(spec.args.join(" ")).toContain("uvicorn anima_server.main:app");
    expect(spec.args.join(" ")).not.toContain("--reload");
    expect(spec.cwd).toBe("C:\\repo\\apps\\server");
  });

  test("starts desktop and anima-mod only after server health is ready", async () => {
    const events: string[] = [];
    const children: ChildStub[] = [];

    const stack = await startDevStack({
      ensureServerPortAvailable: async (url) => {
        events.push(`port:${url}`);
      },
      spawn: ({ name }) => {
        events.push(`spawn:${name}`);
        const child = makeChild(children.length + 1);
        children.push(child);
        return child;
      },
      waitForHealth: async (url) => {
        events.push(`health:${url}`);
      },
    });

    expect(events).toEqual([
      "port:http://127.0.0.1:3031/health",
      "spawn:server",
      "health:http://127.0.0.1:3031/health",
      "spawn:desktop",
      "spawn:anima-mod",
    ]);
    expect(children).toHaveLength(3);

    stack.stop();

    expect(children.map((child) => child.killed)).toEqual([true, true, true]);
  });

  test("stops the server if runtime health never becomes ready", async () => {
    const events: string[] = [];
    const children: ChildStub[] = [];

    await expect(
      startDevStack({
        ensureServerPortAvailable: async () => {},
        spawn: ({ name }) => {
          events.push(`spawn:${name}`);
          const child = makeChild(children.length + 1);
          children.push(child);
          return child;
        },
        waitForHealth: async () => {
          throw new Error("runtime not ready");
        },
      }),
    ).rejects.toThrow("runtime not ready");

    expect(events).toEqual(["spawn:server"]);
    expect(children).toHaveLength(1);
    expect(children[0]?.killed).toBe(true);
  });

  test("does not start dev children when the runtime port is already occupied", async () => {
    const events: string[] = [];

    await expect(
      startDevStack({
        ensureServerPortAvailable: async (url) => {
          events.push(`port:${url}`);
          throw new Error("Runtime port 3031 is already in use");
        },
        spawn: ({ name }) => {
          events.push(`spawn:${name}`);
          return makeChild(1);
        },
      }),
    ).rejects.toThrow("Runtime port 3031 is already in use");

    expect(events).toEqual(["port:http://127.0.0.1:3031/health"]);
  });

  test("exposes a completion promise that resolves when a child exits", async () => {
    const children: ChildStub[] = [];

    const stack = await startDevStack({
      ensureServerPortAvailable: async () => {},
      spawn: () => {
        const child = makeChild(children.length + 1);
        children.push(child);
        return child;
      },
      waitForHealth: async () => {},
    });

    children[1]?.emit("exit", 0, null);

    await expect(stack.completion).resolves.toEqual({
      code: 0,
      name: "desktop",
      signal: null,
    });
  });
});

describe("dev session continuity", () => {
  test("creates an ephemeral server-only environment and removes it on cleanup", () => {
    const tempRoot = mkdtempSync(path.join(tmpdir(), "anima-continuity-test-"));
    try {
      const continuity = createDevSessionContinuity({
        tempRoot,
        randomBytesImpl: () => Buffer.alloc(32, 7),
      });

      expect(continuity.serverEnv.ANIMA_DEV_SESSION_STATE_PATH).toStartWith(
        continuity.directory,
      );
      expect(continuity.serverEnv.ANIMA_DEV_SESSION_KEY).toBe(
        Buffer.alloc(32, 7).toString("base64"),
      );
      expect(existsSync(continuity.directory)).toBe(true);

      const baseEnv = { PATH: "test-path" };
      expect(
        buildTargetEnvironment("server", baseEnv, continuity.serverEnv),
      ).toEqual({ ...baseEnv, ...continuity.serverEnv });
      expect(
        buildTargetEnvironment("desktop", baseEnv, continuity.serverEnv),
      ).toEqual(baseEnv);
      expect(
        buildTargetEnvironment("anima-mod", baseEnv, continuity.serverEnv),
      ).toEqual(baseEnv);
      expect(baseEnv).toEqual({ PATH: "test-path" });

      continuity.cleanup();
      continuity.cleanup();
      expect(existsSync(continuity.directory)).toBe(false);
    } finally {
      rmSync(tempRoot, { recursive: true, force: true });
    }
  });
});

describe("server reload scheduler", () => {
  test("coalesces rapid saves and waits for health readiness", async () => {
    const events: string[] = [];
    const scheduler = createServerReloadScheduler({
      quietMs: 5,
      restart: async () => {
        events.push("restart");
      },
      waitForReady: async () => {
        events.push("ready");
      },
      onError: (error) => {
        throw error;
      },
    });

    scheduler.schedule();
    scheduler.schedule();
    await scheduler.whenIdle();

    expect(events).toEqual(["restart", "ready"]);
    scheduler.stop();
  });

  test("runs one later reload for saves received during an active reload", async () => {
    const events: string[] = [];
    let releaseFirstReload!: () => void;
    const firstReloadGate = new Promise<void>((resolve) => {
      releaseFirstReload = resolve;
    });
    let markFirstStarted!: () => void;
    const firstStarted = new Promise<void>((resolve) => {
      markFirstStarted = resolve;
    });
    let restartCount = 0;
    const scheduler = createServerReloadScheduler({
      quietMs: 5,
      restart: async () => {
        restartCount += 1;
        events.push(`restart:${restartCount}`);
        if (restartCount === 1) {
          markFirstStarted();
          await firstReloadGate;
        }
      },
      waitForReady: async () => {
        events.push(`ready:${restartCount}`);
      },
      onError: (error) => {
        throw error;
      },
    });

    scheduler.schedule();
    await firstStarted;
    scheduler.schedule();
    scheduler.schedule();
    releaseFirstReload();
    await scheduler.whenIdle();

    expect(events).toEqual([
      "restart:1",
      "ready:1",
      "restart:2",
      "ready:2",
    ]);
    scheduler.stop();
  });

  test("stop cancels queued work and reload errors reach the handler", async () => {
    const cancelledEvents: string[] = [];
    const cancelled = createServerReloadScheduler({
      quietMs: 10,
      restart: async () => {
        cancelledEvents.push("restart");
      },
      waitForReady: async () => {},
      onError: (error) => {
        throw error;
      },
    });
    cancelled.schedule();
    cancelled.stop();
    await cancelled.whenIdle();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(cancelledEvents).toEqual([]);

    const failures: Error[] = [];
    const failed = createServerReloadScheduler({
      quietMs: 5,
      restart: async () => {
        throw new Error("reload failed");
      },
      waitForReady: async () => {
        throw new Error("must not run");
      },
      onError: (error) => {
        failures.push(error instanceof Error ? error : new Error(String(error)));
      },
    });
    failed.schedule();
    await failed.whenIdle();
    expect(failures.map((error) => error.message)).toEqual(["reload failed"]);
    failed.stop();
  });
});

describe("getServerRestartCandidate", () => {
  test("returns the normalized source path for restartable server files", () => {
    expect(getServerRestartCandidate("src\\anima_server\\main.py")).toBe(
      "src/anima_server/main.py",
    );
    expect(getServerRestartCandidate("pyproject.toml")).toBe("pyproject.toml");
    expect(getServerRestartCandidate("alembic.ini")).toBe("alembic.ini");
  });

  test("ignores generated Python cache files", () => {
    expect(getServerRestartCandidate("__pycache__\\main.cpython-312.pyc")).toBeNull();
    expect(
      getServerRestartCandidate("src\\anima_server\\__pycache__\\main.py"),
    ).toBeNull();
  });

  test("ignores unrelated paths", () => {
    expect(getServerRestartCandidate(null)).toBeNull();
    expect(getServerRestartCandidate("src/anima_server/main.pyc")).toBeNull();
    expect(getServerRestartCandidate("README.md")).toBeNull();
  });
});
