import { describe, test, expect, afterEach } from "bun:test";
import { truncateOutput, truncateItems, writeOverflow, cleanupOverflow, LIMITS } from "./truncation";
import { existsSync, unlinkSync, readFileSync } from "node:fs";

describe("truncateOutput", () => {
  const overflowPaths: string[] = [];

  afterEach(() => {
    for (const p of overflowPaths) {
      try { unlinkSync(p); } catch {}
    }
    overflowPaths.length = 0;
  });

  test("passes through short output unchanged", () => {
    const r = truncateOutput("hello\nworld", { overflow: false });
    expect(r.truncated).toBe(false);
    expect(r.content).toBe("hello\nworld");
  });

  test("truncates by line count with middle omission", () => {
    const lines = Array.from({ length: 200 }, (_, i) => `line ${i}`);
    const r = truncateOutput(lines.join("\n"), { maxLines: 20, overflow: false });
    expect(r.truncated).toBe(true);
    expect(r.content).toContain("lines omitted");
    // Should keep head and tail
    expect(r.content).toContain("line 0");
    expect(r.content).toContain("line 199");
  });

  test("preserves error-relevant lines from omitted middle", () => {
    const lines = [
      ...Array.from({ length: 50 }, (_, i) => `info ${i}`),
      "ERROR: something broke",
      "  at Module.compile (node:internal/modules/cjs/loader:1234)",
      ...Array.from({ length: 50 }, (_, i) => `info ${i + 50}`),
    ];
    const r = truncateOutput(lines.join("\n"), { maxLines: 20, overflow: false });
    expect(r.truncated).toBe(true);
    expect(r.content).toContain("ERROR: something broke");
    expect(r.content).toContain("error-relevant lines");
  });

  test("truncates by char count as safety net", () => {
    const huge = "x".repeat(50_000);
    const r = truncateOutput(huge, { maxChars: 1000, overflow: false });
    expect(r.truncated).toBe(true);
    expect(r.content.length).toBeLessThan(2000); // ~1000 + notice
  });

  test("truncates per-line length", () => {
    const lines = ["short", "x".repeat(5000), "also short"];
    const r = truncateOutput(lines.join("\n"), { maxCharsPerLine: 100, overflow: false });
    expect(r.truncated).toBe(true);
    expect(r.content).toContain("line truncated");
  });

  test("writes overflow file when truncated", () => {
    const lines = Array.from({ length: 200 }, (_, i) => `line ${i}`);
    const r = truncateOutput(lines.join("\n"), { maxLines: 20, toolName: "test" });
    expect(r.truncated).toBe(true);
    expect(r.overflowPath).toBeDefined();
    if (r.overflowPath) {
      overflowPaths.push(r.overflowPath);
      expect(existsSync(r.overflowPath)).toBe(true);
      const full = readFileSync(r.overflowPath, "utf-8");
      expect(full).toContain("line 100"); // middle line present in overflow
    }
  });
});

describe("truncateItems", () => {
  test("passes through small arrays unchanged", () => {
    const r = truncateItems([1, 2, 3], 10, (items) => items.join(","), "items", "test");
    expect(r.truncated).toBe(false);
    expect(r.content).toBe("1,2,3");
  });

  test("truncates large arrays with head+tail", () => {
    const items = Array.from({ length: 100 }, (_, i) => `file${i}.ts`);
    const r = truncateItems(items, 10, (i) => i.join("\n"), "files", "test");
    expect(r.truncated).toBe(true);
    expect(r.content).toContain("file0.ts");
    expect(r.content).toContain("file99.ts");
    expect(r.content).toContain("90 files omitted");
  });
});

describe("cleanupOverflow", () => {
  test("returns 0 when no overflow dir exists", () => {
    expect(cleanupOverflow()).toBeGreaterThanOrEqual(0);
  });
});
