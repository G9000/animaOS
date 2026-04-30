import { describe, test, expect } from "bun:test";
import { parseCliArgs } from "./cli";

describe("Animus CLI args", () => {
  test("parses per-run workspace override without treating it as the prompt", () => {
    const parsed = parseCliArgs(["--workspace", "C:\\anima-work", "summarize"]);

    expect(parsed.workspaceOverride).toBe("C:\\anima-work");
    expect(parsed.prompt).toBe("summarize");
  });

  test("requires a workspace value after --workspace", () => {
    expect(() => parseCliArgs(["--workspace"])).toThrow(
      "--workspace requires a directory argument",
    );
  });

  test("parses config show command", () => {
    expect(parseCliArgs(["config", "show"]).configCommand).toEqual({
      type: "show",
    });
  });

  test("parses config set-workspace command", () => {
    expect(parseCliArgs(["config", "set-workspace", "C:\\anima-work"]).configCommand).toEqual({
      type: "set-workspace",
      workspaceDir: "C:\\anima-work",
    });
  });
});
