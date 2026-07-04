import { describe, expect, test } from "bun:test";

import { buildTargetSpec, startDevStack } from "../scripts/dev-root-lib.mjs";

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

  test("exposes a completion promise that resolves when a child exits", async () => {
    const children: ChildStub[] = [];

    const stack = await startDevStack({
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
