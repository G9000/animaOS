import { describe, test, expect, beforeEach } from "bun:test";
import { hooks } from "./hooks";

describe("hooks", () => {
  beforeEach(() => {
    hooks.clear();
  });

  test("registers and emits events", async () => {
    const calls: string[] = [];
    hooks.on("session:start", (data) => {
      calls.push(`start:${data.username}`);
    });

    await hooks.emit("session:start", { serverUrl: "ws://localhost", username: "alice" });
    expect(calls).toEqual(["start:alice"]);
  });

  test("supports multiple listeners", async () => {
    let count = 0;
    hooks.on("session:end", () => { count++; });
    hooks.on("session:end", () => { count++; });

    await hooks.emit("session:end", { reason: "user" });
    expect(count).toBe(2);
  });

  test("unsubscribe removes listener", async () => {
    let count = 0;
    const unsub = hooks.on("session:end", () => { count++; });

    await hooks.emit("session:end", { reason: "user" });
    expect(count).toBe(1);

    unsub();
    await hooks.emit("session:end", { reason: "user" });
    expect(count).toBe(1); // not called again
  });

  test("tool:before and tool:after fire in order", async () => {
    const events: string[] = [];
    hooks.on("tool:before", (d) => { events.push(`before:${d.toolName}`); });
    hooks.on("tool:after", (d) => { events.push(`after:${d.toolName}:${d.status}`); });

    await hooks.emit("tool:before", { toolName: "bash", args: {}, toolCallId: "1" });
    await hooks.emit("tool:after", { toolName: "bash", args: {}, toolCallId: "1", status: "success", durationMs: 42 });

    expect(events).toEqual(["before:bash", "after:bash:success"]);
  });

  test("listener errors are caught and emitted as error events", async () => {
    const errors: string[] = [];
    hooks.on("session:start", () => { throw new Error("boom"); });
    hooks.on("error", (d) => { errors.push(d.error.message); });

    await hooks.emit("session:start", { serverUrl: "ws://localhost", username: "bob" });
    expect(errors).toEqual(["boom"]);
  });

  test("error handler errors don't infinite loop", async () => {
    // This should write to stderr but not throw or loop
    hooks.on("error", () => { throw new Error("meta-boom"); });
    await hooks.emit("error", { error: new Error("original"), source: "test" });
    // If we get here, no infinite loop occurred
    expect(true).toBe(true);
  });

  test("clear removes all listeners", async () => {
    hooks.on("session:start", () => {});
    hooks.on("session:end", () => {});
    expect(hooks.listenerCount("session:start")).toBe(1);

    hooks.clear();
    expect(hooks.listenerCount("session:start")).toBe(0);
    expect(hooks.listenerCount("session:end")).toBe(0);
  });

  test("emit with no listeners is a no-op", async () => {
    // Should not throw
    await hooks.emit("session:start", { serverUrl: "ws://localhost", username: "nobody" });
  });

  test("message:received fires with type", async () => {
    const types: string[] = [];
    hooks.on("message:received", (d) => { types.push(d.type); });

    await hooks.emit("message:received", { type: "stream_token", token: "hi" });
    await hooks.emit("message:received", { type: "turn_complete" });

    expect(types).toEqual(["stream_token", "turn_complete"]);
  });
});
