import { describe, test, expect, beforeEach, afterEach } from "bun:test";
import { executeTool } from "./executor";
import type { ToolExecuteMessage } from "../client/protocol";
import { clearSessionRules } from "./permissions";
import { mkdtempSync, rmSync, writeFileSync, readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

function makeMsg(
  tool_name: string,
  args: Record<string, unknown>,
): ToolExecuteMessage {
  return { type: "tool_execute", tool_call_id: `test-${Date.now()}`, tool_name, args };
}

describe("executor", () => {
  let tempDir: string;

  beforeEach(() => {
    tempDir = mkdtempSync(join(tmpdir(), "animus-executor-test-"));
    clearSessionRules();
  });

  afterEach(() => {
    rmSync(tempDir, { recursive: true, force: true });
  });

  test("dispatches to bash", async () => {
    const result = await executeTool(makeMsg("bash", { command: "echo executor-test" }));
    expect(result.status).toBe("success");
    expect(result.result).toContain("executor-test");
  });

  test("dispatches to read_file", async () => {
    const filePath = join(tempDir, "read-me.txt");
    writeFileSync(filePath, "executor content", "utf-8");

    const result = await executeTool(makeMsg("read_file", { file_path: filePath }));
    expect(result.status).toBe("success");
    expect(result.result).toContain("executor content");
  });

  test("dispatches to write_file", async () => {
    const filePath = join(tempDir, "written.txt");
    const result = await executeTool(
      makeMsg("write_file", { file_path: filePath, content: "test output" }),
    );
    expect(result.status).toBe("success");
    expect(existsSync(filePath)).toBe(true);
    expect(readFileSync(filePath, "utf-8")).toBe("test output");
  });

  test("dispatches to list_dir", async () => {
    writeFileSync(join(tempDir, "a.txt"), "", "utf-8");
    const result = await executeTool(makeMsg("list_dir", { path: tempDir }));
    expect(result.status).toBe("success");
    expect(result.result).toContain("[file] a.txt");
  });

  test("dispatches to glob", async () => {
    writeFileSync(join(tempDir, "match.ts"), "", "utf-8");
    const result = await executeTool(
      makeMsg("glob", { pattern: "*.ts", path: tempDir }),
    );
    expect(result.status).toBe("success");
    expect(result.result).toContain("match.ts");
  });

  test("dispatches to edit_file", async () => {
    const filePath = join(tempDir, "editable.txt");
    writeFileSync(filePath, "old content here", "utf-8");

    const result = await executeTool(
      makeMsg("edit_file", {
        file_path: filePath,
        old_string: "old",
        new_string: "new",
      }),
    );
    expect(result.status).toBe("success");
    expect(readFileSync(filePath, "utf-8")).toBe("new content here");
  });

  test("permission ask + deny callback returns error", async () => {
    // curl is unknown to safe patterns, so it gets "ask"
    const result = await executeTool(
      makeMsg("bash", { command: "curl https://example.com" }),
      async () => "deny",
    );
    expect(result.status).toBe("error");
    expect(result.result).toContain("User denied");
  });

  test("permission ask + allow callback proceeds", async () => {
    const result = await executeTool(
      makeMsg("bash", { command: "echo allowed-cmd" }),
      async () => "allow",
    );
    expect(result.status).toBe("success");
    expect(result.result).toContain("allowed-cmd");
  });

  test("unknown tool returns error", async () => {
    const result = await executeTool(makeMsg("nonexistent_tool", {}));
    expect(result.status).toBe("error");
    expect(result.result).toContain("Unknown tool: nonexistent_tool");
  });

  test("tool error is caught and wrapped", async () => {
    const result = await executeTool(
      makeMsg("read_file", { file_path: join(tempDir, "does-not-exist.txt") }),
    );
    expect(result.status).toBe("error");
    expect(result.result).toContain("File not found");
  });

  test("preserves tool_call_id in result", async () => {
    const msg = makeMsg("bash", { command: "echo hi" });
    const result = await executeTool(msg);
    expect(result.tool_call_id).toBe(msg.tool_call_id);
  });
});
