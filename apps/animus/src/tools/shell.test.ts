// apps/animus/src/tools/shell.test.ts
import { describe, test, expect } from "bun:test";
import { getShellLauncher, type ShellLauncher, _resetCache } from "./shell";

describe("shell launcher", () => {
  test("returns a non-empty array", () => {
    const launcher = getShellLauncher();
    expect(launcher.length).toBeGreaterThanOrEqual(2);
  });

  test("first element is an executable path or name", () => {
    const launcher = getShellLauncher();
    expect(typeof launcher[0]).toBe("string");
    expect(launcher[0].length).toBeGreaterThan(0);
  });

  test("second element is a command flag", () => {
    const launcher = getShellLauncher();
    const flag = launcher[1];
    expect(["-c", "-lc", "-NoProfile", "/d"]).toContain(flag);
  });

  test("caches the result across calls", () => {
    const a = getShellLauncher();
    const b = getShellLauncher();
    expect(a).toBe(b); // reference equality
  });

  test("launcher can execute a simple command", async () => {
    const launcher = getShellLauncher();
    const proc = Bun.spawn([...launcher, "echo hello"], {
      stdout: "pipe",
      stderr: "pipe",
    });
    await proc.exited;
    const out = await new Response(proc.stdout).text();
    expect(out.trim()).toBe("hello");
    expect(proc.exitCode).toBe(0);
  });

  test("reset clears the cache", () => {
    const a = getShellLauncher();
    _resetCache();
    const b = getShellLauncher();
    expect(a).not.toBe(b);
    expect(b.length).toBeGreaterThanOrEqual(2);
  });
});
