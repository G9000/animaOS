// apps/animus/src/tools/edit_hints.test.ts
import { describe, test, expect } from "bun:test";
import {
  unescapeOverEscaped,
  buildNotFoundError,
  normalizeLineEndings,
} from "./edit_hints";

describe("normalizeLineEndings", () => {
  test("converts \\r\\n to \\n", () => {
    expect(normalizeLineEndings("a\r\nb\r\nc")).toBe("a\nb\nc");
  });

  test("leaves \\n unchanged", () => {
    expect(normalizeLineEndings("a\nb")).toBe("a\nb");
  });
});

describe("unescapeOverEscaped", () => {
  test("unescapes \\\\n to newline", () => {
    expect(unescapeOverEscaped("line1\\nline2")).toBe("line1\nline2");
  });

  test("unescapes \\\\t to tab", () => {
    expect(unescapeOverEscaped("col1\\tcol2")).toBe("col1\tcol2");
  });

  test("unescapes mixed", () => {
    expect(unescapeOverEscaped('say \\"hello\\"')).toBe('say "hello"');
  });

  test("returns unchanged string when nothing to unescape", () => {
    const s = "normal string";
    expect(unescapeOverEscaped(s)).toBe(s);
  });

  test("unescapes backslash", () => {
    expect(unescapeOverEscaped("path\\\\to\\\\file")).toBe("path\\to\\file");
  });
});

describe("buildNotFoundError", () => {
  test("detects smart quote mismatch", () => {
    const content = "it\u2019s a test with \u201Cquotes\u201D";
    const oldString = 'it\'s a test with "quotes"';
    const err = buildNotFoundError("/tmp/f.txt", oldString, content);
    expect(err).toContain("smart");
  });

  test("detects whitespace mismatch", () => {
    const content = "  function  foo() {\n    return 1;\n  }";
    const oldString = "function foo() {\n  return 1;\n}";
    const err = buildNotFoundError("/tmp/f.txt", oldString, content);
    expect(err).toContain("whitespace");
  });

  test("returns fallback when no hint applies", () => {
    const content = "completely different content";
    const oldString = "nothing matches at all xyz";
    const err = buildNotFoundError("/tmp/f.txt", oldString, content);
    expect(err).toContain("not found");
    expect(err).toContain("/tmp/f.txt");
  });

  test("smart quote check handles curly single quotes", () => {
    const content = "don\u2018t stop";
    const oldString = "don't stop";
    const err = buildNotFoundError("/tmp/f.txt", oldString, content);
    expect(err).toContain("smart");
  });
});
