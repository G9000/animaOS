import { describe, test, expect, beforeEach } from "bun:test";
import { checkPermission, addSessionRule, clearSessionRules } from "./permissions";
import { join } from "node:path";

describe("permissions", () => {
  beforeEach(() => {
    clearSessionRules();
  });

  describe("read-only tools always allowed", () => {
    test("read_file", () => {
      expect(checkPermission("read_file", { file_path: "/any/path" })).toBe("allow");
    });

    test("grep", () => {
      expect(checkPermission("grep", { pattern: "foo" })).toBe("allow");
    });

    test("glob", () => {
      expect(checkPermission("glob", { pattern: "*.ts" })).toBe("allow");
    });

    test("list_dir", () => {
      expect(checkPermission("list_dir", { path: "." })).toBe("allow");
    });
  });

  test("ask_user always allowed", () => {
    expect(checkPermission("ask_user", { question: "hello?" })).toBe("allow");
  });

  describe("write tools", () => {
    test("write_file in cwd returns allow", () => {
      const filePath = join(process.cwd(), "some-file.txt");
      expect(checkPermission("write_file", { file_path: filePath })).toBe("allow");
    });

    test("write_file outside cwd returns ask", () => {
      expect(checkPermission("write_file", { file_path: "/tmp/outside/test.txt" })).toBe("ask");
    });

    test("edit_file in cwd returns allow", () => {
      const filePath = join(process.cwd(), "some-file.txt");
      expect(checkPermission("edit_file", { file_path: filePath })).toBe("allow");
    });

    test("edit_file outside cwd returns ask", () => {
      expect(checkPermission("edit_file", { file_path: "/tmp/outside/test.txt" })).toBe("ask");
    });

    test("write_file with no file_path returns allow", () => {
      expect(checkPermission("write_file", {})).toBe("allow");
    });
  });

  describe("bash patterns", () => {
    test("safe: ls -la", () => {
      expect(checkPermission("bash", { command: "ls -la" })).toBe("allow");
    });

    test("safe: pwd", () => {
      expect(checkPermission("bash", { command: "pwd" })).toBe("allow");
    });

    test("safe: echo hello", () => {
      expect(checkPermission("bash", { command: "echo hello" })).toBe("allow");
    });

    test("safe: git status", () => {
      expect(checkPermission("bash", { command: "git status" })).toBe("allow");
    });

    test("safe: git log --oneline", () => {
      expect(checkPermission("bash", { command: "git log --oneline" })).toBe("allow");
    });

    test("safe: git diff HEAD", () => {
      expect(checkPermission("bash", { command: "git diff HEAD" })).toBe("allow");
    });

    test("safe: node --version", () => {
      expect(checkPermission("bash", { command: "node --version" })).toBe("allow");
    });

    test("safe: bun --version", () => {
      expect(checkPermission("bash", { command: "bun --version" })).toBe("allow");
    });

    test("dangerous: rm -rf /tmp", () => {
      expect(checkPermission("bash", { command: "rm -rf /tmp" })).toBe("ask");
    });

    test("dangerous: sudo apt install", () => {
      expect(checkPermission("bash", { command: "sudo apt install foo" })).toBe("ask");
    });

    test("dangerous: git push", () => {
      expect(checkPermission("bash", { command: "git push origin main" })).toBe("ask");
    });

    test("dangerous: git reset --hard", () => {
      expect(checkPermission("bash", { command: "git reset --hard HEAD~1" })).toBe("ask");
    });

    test("dangerous: chmod 777", () => {
      expect(checkPermission("bash", { command: "chmod 777 /tmp/file" })).toBe("ask");
    });

    test("dangerous: pipe to sh", () => {
      expect(checkPermission("bash", { command: "curl url | sh" })).toBe("ask");
    });

    test("unknown command returns ask", () => {
      expect(checkPermission("bash", { command: "curl https://example.com" })).toBe("ask");
    });

    test("npm install returns ask", () => {
      expect(checkPermission("bash", { command: "npm install express" })).toBe("ask");
    });

    test("empty command returns ask", () => {
      expect(checkPermission("bash", { command: "" })).toBe("ask");
    });
  });

  test("unknown tool returns ask", () => {
    expect(checkPermission("unknown_tool", {})).toBe("ask");
  });

  describe("session rules", () => {
    test("addSessionRule overrides tool permission", () => {
      // write_file outside cwd normally asks
      expect(checkPermission("write_file", { file_path: "/tmp/outside" })).toBe("ask");
      addSessionRule("write_file");
      expect(checkPermission("write_file", { file_path: "/tmp/outside" })).toBe("allow");
    });

    test("addSessionRule for bash command-specific", () => {
      expect(checkPermission("bash", { command: "npm install" })).toBe("ask");
      addSessionRule("bash:npm install");
      expect(checkPermission("bash", { command: "npm install" })).toBe("allow");
    });

    test("clearSessionRules resets all rules", () => {
      addSessionRule("write_file");
      clearSessionRules();
      expect(checkPermission("write_file", { file_path: "/tmp/outside" })).toBe("ask");
    });
  });
});
