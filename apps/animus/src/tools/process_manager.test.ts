import { describe, test, expect, afterEach } from "bun:test";
import { executeBgStart, executeBgOutput, executeBgStop, executeBgList, resetProcesses } from "./process_manager";

afterEach(() => {
  resetProcesses();
});

describe("bg_start", () => {
  test("starts a process and returns an id", () => {
    const result = executeBgStart({ command: "echo hello && sleep 0.1" });
    expect(result.status).toBe("success");
    expect(result.result).toContain("bg-");
    expect(result.result).toContain("bg_output");
  });
});

describe("bg_list", () => {
  test("shows empty when no processes", () => {
    const result = executeBgList();
    expect(result.result).toContain("No background");
  });

  test("lists running processes", () => {
    executeBgStart({ command: "sleep 10" });
    const result = executeBgList();
    expect(result.result).toContain("bg-");
    expect(result.result).toContain("sleep 10");
  });
});

describe("bg_output", () => {
  test("returns error for unknown id", () => {
    const result = executeBgOutput({ id: "bg-999" });
    expect(result.status).toBe("error");
  });

  test("reads incremental output", async () => {
    const start = executeBgStart({ command: "echo line1 && echo line2 && sleep 0.1" });
    const id = start.result.match(/bg-\d+/)![0];

    // Give it a moment to produce output
    await new Promise((r) => setTimeout(r, 300));

    const r1 = executeBgOutput({ id });
    expect(r1.status).toBe("success");
    expect(r1.result).toContain("line1");

    // Second read should show no new output
    const r2 = executeBgOutput({ id });
    expect(r2.result).toContain("No new output");
  });

  test("all flag returns full output", async () => {
    const start = executeBgStart({ command: "echo aaa && echo bbb && sleep 0.1" });
    const id = start.result.match(/bg-\d+/)![0];

    await new Promise((r) => setTimeout(r, 300));

    // First read consumes output
    executeBgOutput({ id });

    // all=true should still return everything
    const r = executeBgOutput({ id, all: true });
    expect(r.result).toContain("aaa");
    expect(r.result).toContain("bbb");
  });
});

describe("bg_stop", () => {
  test("kills a running process", async () => {
    const start = executeBgStart({ command: "sleep 60" });
    const id = start.result.match(/bg-\d+/)![0];

    const stop = executeBgStop({ id });
    expect(stop.status).toBe("success");
    expect(stop.result).toContain("Stopped");

    // Should be gone from list
    const list = executeBgList();
    expect(list.result).toContain("No background");
  });

  test("returns error for unknown id", () => {
    const result = executeBgStop({ id: "bg-999" });
    expect(result.status).toBe("error");
  });
});
