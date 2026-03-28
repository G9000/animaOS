# Animus Tool Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add cross-platform shell launching, pre-dispatch arg validation, and smart edit error diagnostics to animus tools.

**Architecture:** Three independent modules that plug into the existing tool infrastructure. Shell launcher replaces hardcoded `["bash", "-c"]` in two files. Validation gates the executor before dispatch. Edit hints enrich the existing edit/multi_edit error paths.

**Tech Stack:** TypeScript, Bun runtime, bun:test

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/tools/shell.ts` | **New** — Platform-aware shell launcher with caching |
| `src/tools/shell.test.ts` | **New** — Shell launcher tests |
| `src/tools/validation.ts` | **New** — Tool arg type validation against JSON schema |
| `src/tools/validation.test.ts` | **New** — Validation tests |
| `src/tools/edit_hints.ts` | **New** — `unescapeOverEscaped()`, `buildNotFoundError()`, `normalizeLineEndings()` |
| `src/tools/edit_hints.test.ts` | **New** — Edit hint tests |
| `src/tools/edit.ts` | **Modified** — Wire in normalization, over-escape auto-fix, diagnostic hints |
| `src/tools/multi_edit.ts` | **Modified** — Wire in same edit improvements |
| `src/tools/bash.ts` | **Modified** — Use `getShellLauncher()` |
| `src/tools/process_manager.ts` | **Modified** — Use `getShellLauncher()` |
| `src/tools/executor.ts` | **Modified** — Add `validateArgs()` call before dispatch |
| `src/tools/registry.ts` | **Modified** — Export `TOOL_SCHEMA_MAP` |

---

### Task 1: Shell Launcher — Tests

**Files:**
- Create: `apps/animus/src/tools/shell.test.ts`

- [ ] **Step 1: Write shell launcher tests**

```typescript
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
    // Should be -c, -lc, -Command, or /d
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
    // Both should work, but not be same reference
    expect(a).not.toBe(b);
    expect(b.length).toBeGreaterThanOrEqual(2);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/animus && bun test src/tools/shell.test.ts`
Expected: FAIL — `Cannot find module "./shell"`

- [ ] **Step 3: Commit**

```bash
git add apps/animus/src/tools/shell.test.ts
git commit -m "test: add shell launcher tests"
```

---

### Task 2: Shell Launcher — Implementation

**Files:**
- Create: `apps/animus/src/tools/shell.ts`

- [ ] **Step 1: Implement shell launcher**

```typescript
// apps/animus/src/tools/shell.ts
import { existsSync } from "node:fs";

export type ShellLauncher = [executable: string, ...flags: string[]];

let cached: ShellLauncher | null = null;

/** Returns [executable, ...flags] for Bun.spawn([...launcher, command]). Cached for process lifetime. */
export function getShellLauncher(): ShellLauncher {
  if (cached) return cached;
  cached = resolve();
  return cached;
}

/** Reset cache — for testing only. */
export function _resetCache(): void {
  cached = null;
}

function resolve(): ShellLauncher {
  const platform = process.platform;

  if (platform === "win32") {
    return resolveWindows();
  }
  if (platform === "darwin") {
    return resolveDarwin();
  }
  return resolveLinux();
}

function resolveDarwin(): ShellLauncher {
  // Prefer zsh on macOS — avoids bash 3.2 HEREDOC apostrophe bug
  const candidates: Array<[string, string[]]> = [
    ["/bin/zsh", ["-lc"]],
    ["/bin/bash", ["-c"]],
  ];
  return probeFirst(candidates);
}

function resolveLinux(): ShellLauncher {
  const candidates: Array<[string, string[]]> = [];

  // Respect $SHELL if set
  const userShell = process.env.SHELL;
  if (userShell) {
    const flags = shellFlags(userShell);
    candidates.push([userShell, flags]);
  }

  candidates.push(
    ["/bin/bash", ["-c"]],
    ["/usr/bin/bash", ["-c"]],
    ["/bin/zsh", ["-c"]],
    ["/bin/sh", ["-c"]],
  );
  return probeFirst(candidates);
}

function resolveWindows(): ShellLauncher {
  const candidates: Array<[string, string[]]> = [
    ["powershell.exe", ["-NoProfile", "-Command"]],
    ["pwsh", ["-NoProfile", "-Command"]],
  ];

  // Respect ComSpec for cmd.exe
  const comspec = process.env.ComSpec || "cmd.exe";
  candidates.push([comspec, ["/d", "/s", "/c"]]);

  return probeFirst(candidates);
}

function shellFlags(shell: string): string[] {
  const base = shell.split("/").pop() || "";
  if (base === "bash" || base === "zsh") return ["-lc"];
  return ["-c"];
}

function probeFirst(candidates: Array<[string, string[]]>): ShellLauncher {
  const tried: string[] = [];
  for (const [exe, flags] of candidates) {
    tried.push(exe);
    if (exe.startsWith("/")) {
      if (existsSync(exe)) return [exe, ...flags];
    } else {
      // For non-absolute paths (Windows), check via Bun.which
      if (typeof Bun !== "undefined" && Bun.which(exe)) return [exe, ...flags];
      // Fallback: assume it exists (will fail at spawn time with clear error)
      return [exe, ...flags];
    }
  }
  throw new Error(`No shell found. Tried: ${tried.join(", ")}`);
}
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd apps/animus && bun test src/tools/shell.test.ts`
Expected: All 6 tests PASS

- [ ] **Step 3: Commit**

```bash
git add apps/animus/src/tools/shell.ts
git commit -m "feat(animus): add cross-platform shell launcher"
```

---

### Task 3: Wire Shell Launcher into bash.ts and process_manager.ts

**Files:**
- Modify: `apps/animus/src/tools/bash.ts:1-22`
- Modify: `apps/animus/src/tools/process_manager.ts:1-46`

- [ ] **Step 1: Update bash.ts to use getShellLauncher()**

In `apps/animus/src/tools/bash.ts`, add import and replace the spawn call:

```typescript
// Add at line 3 (after existing imports):
import { getShellLauncher } from "./shell";
```

Replace line 22:
```typescript
// Old:
    const proc = Bun.spawn(["bash", "-c", command], {
// New:
    const proc = Bun.spawn([...getShellLauncher(), command], {
```

- [ ] **Step 2: Update process_manager.ts to use getShellLauncher()**

In `apps/animus/src/tools/process_manager.ts`, add import and replace the spawn call:

```typescript
// Add at line 13 (after existing imports):
import { getShellLauncher } from "./shell";
```

Replace line 46:
```typescript
// Old:
  const proc = Bun.spawn(["bash", "-c", command], {
// New:
  const proc = Bun.spawn([...getShellLauncher(), command], {
```

- [ ] **Step 3: Run existing tests to verify nothing broke**

Run: `cd apps/animus && bun test src/tools/bash.test.ts src/tools/process_manager.test.ts`
Expected: All existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add apps/animus/src/tools/bash.ts apps/animus/src/tools/process_manager.ts
git commit -m "refactor(animus): use shell launcher in bash and process_manager"
```

---

### Task 4: Arg Validation — Tests

**Files:**
- Create: `apps/animus/src/tools/validation.test.ts`

- [ ] **Step 1: Write validation tests**

```typescript
// apps/animus/src/tools/validation.test.ts
import { describe, test, expect } from "bun:test";
import { validateArgs } from "./validation";

describe("validateArgs", () => {
  const bashSchema = {
    type: "object",
    properties: {
      command: { type: "string" },
      timeout: { type: "number" },
    },
    required: ["command"],
  };

  test("returns null for valid args", () => {
    expect(validateArgs("bash", { command: "echo hi" }, bashSchema)).toBeNull();
  });

  test("returns null when optional params omitted", () => {
    expect(validateArgs("bash", { command: "ls" }, bashSchema)).toBeNull();
  });

  test("returns error for missing required param", () => {
    const err = validateArgs("bash", {}, bashSchema);
    expect(err).toContain("missing required parameter");
    expect(err).toContain("command");
  });

  test("returns error for wrong type", () => {
    const err = validateArgs("bash", { command: 123 }, bashSchema);
    expect(err).toContain("must be a string");
    expect(err).toContain("received number");
  });

  test("ignores unknown params (forward-compat)", () => {
    expect(
      validateArgs("bash", { command: "ls", extra: true }, bashSchema),
    ).toBeNull();
  });

  test("validates boolean type", () => {
    const schema = {
      type: "object",
      properties: { all: { type: "boolean" } },
      required: ["all"],
    };
    expect(validateArgs("bg_output", { all: true }, schema)).toBeNull();
    const err = validateArgs("bg_output", { all: "yes" }, schema);
    expect(err).toContain("must be a boolean");
  });

  test("validates array type", () => {
    const schema = {
      type: "object",
      properties: {
        edits: { type: "array", items: { type: "object" } },
      },
      required: ["edits"],
    };
    expect(
      validateArgs("multi_edit", { edits: [{ old_string: "a", new_string: "b" }] }, schema),
    ).toBeNull();

    const err = validateArgs("multi_edit", { edits: "not an array" }, schema);
    expect(err).toContain("must be an array");
  });

  test("validates array element types", () => {
    const schema = {
      type: "object",
      properties: {
        items: { type: "array", items: { type: "string" } },
      },
      required: ["items"],
    };
    const err = validateArgs("test", { items: ["a", 42] }, schema);
    expect(err).toContain("items[1]");
    expect(err).toContain("must be a string");
  });

  test("returns null for schema with no properties", () => {
    const schema = { type: "object", properties: {} };
    expect(validateArgs("todo_read", {}, schema)).toBeNull();
  });

  test("distinguishes integer from number", () => {
    const schema = {
      type: "object",
      properties: { count: { type: "integer" } },
      required: ["count"],
    };
    expect(validateArgs("test", { count: 5 }, schema)).toBeNull();
    const err = validateArgs("test", { count: 5.5 }, schema);
    expect(err).toContain("must be an integer");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/animus && bun test src/tools/validation.test.ts`
Expected: FAIL — `Cannot find module "./validation"`

- [ ] **Step 3: Commit**

```bash
git add apps/animus/src/tools/validation.test.ts
git commit -m "test: add tool arg validation tests"
```

---

### Task 5: Arg Validation — Implementation

**Files:**
- Create: `apps/animus/src/tools/validation.ts`

- [ ] **Step 1: Implement validateArgs**

```typescript
// apps/animus/src/tools/validation.ts

/**
 * Validate tool args against a JSON schema.
 * Returns null if valid, or an error message string.
 */
export function validateArgs(
  toolName: string,
  args: Record<string, unknown>,
  schema: Record<string, unknown>,
): string | null {
  const properties = (schema.properties ?? {}) as Record<string, Record<string, unknown>>;
  const required = (schema.required ?? []) as string[];

  // Check required params
  for (const key of required) {
    if (!(key in args) || args[key] === undefined) {
      return `${toolName}: missing required parameter '${key}'`;
    }
  }

  // Check types of provided params
  for (const [key, value] of Object.entries(args)) {
    const propSchema = properties[key];
    if (!propSchema) continue; // unknown param — ignore for forward-compat

    const expectedType = propSchema.type as string | undefined;
    if (!expectedType) continue;

    const actualType = jsonSchemaType(value);

    // "number" schema accepts both integer and number
    if (expectedType === "number" && (actualType === "number" || actualType === "integer")) {
      continue;
    }

    if (actualType !== expectedType) {
      const article = /^[aeiou]/.test(expectedType) ? "an" : "a";
      return `${toolName}: '${key}' must be ${article} ${expectedType}, received ${actualType}`;
    }

    // Validate array element types
    if (expectedType === "array" && Array.isArray(value)) {
      const itemsSchema = propSchema.items as Record<string, unknown> | undefined;
      const itemType = itemsSchema?.type as string | undefined;
      if (itemType) {
        for (let i = 0; i < value.length; i++) {
          const elemType = jsonSchemaType(value[i]);
          const elemMatches =
            elemType === itemType ||
            (itemType === "number" && (elemType === "number" || elemType === "integer"));
          if (!elemMatches) {
            const art = /^[aeiou]/.test(itemType) ? "an" : "a";
            return `${toolName}: '${key}[${i}]' must be ${art} ${itemType}, received ${elemType}`;
          }
        }
      }
    }
  }

  return null;
}

function jsonSchemaType(value: unknown): string {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  const t = typeof value;
  if (t === "number") return Number.isInteger(value) ? "integer" : "number";
  return t; // "string" | "boolean" | "object" | "undefined"
}
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd apps/animus && bun test src/tools/validation.test.ts`
Expected: All 10 tests PASS

- [ ] **Step 3: Commit**

```bash
git add apps/animus/src/tools/validation.ts
git commit -m "feat(animus): add tool arg type validation"
```

---

### Task 6: Wire Validation into Executor and Registry

**Files:**
- Modify: `apps/animus/src/tools/registry.ts:1-4`
- Modify: `apps/animus/src/tools/executor.ts:1-80`

- [ ] **Step 1: Add TOOL_SCHEMA_MAP export to registry.ts**

At the bottom of `apps/animus/src/tools/registry.ts`, after the `ACTION_TOOL_SCHEMAS` array (after line 274), add:

```typescript
/** Lookup map built from ACTION_TOOL_SCHEMAS for O(1) access by tool name. */
export const TOOL_SCHEMA_MAP = new Map(
  ACTION_TOOL_SCHEMAS.map((s) => [s.name, s]),
);
```

- [ ] **Step 2: Add validation call to executor.ts**

In `apps/animus/src/tools/executor.ts`, add imports after line 16:

```typescript
import { validateArgs } from "./validation";
import { TOOL_SCHEMA_MAP } from "./registry";
```

Then insert validation between the permission check (line 64) and the `tool:before` hook (line 67). After the closing brace of the `else if (decision === "deny")` block, add:

```typescript
  // Validate args against schema before dispatch
  const schema = TOOL_SCHEMA_MAP.get(tool_name);
  if (schema) {
    const validationError = validateArgs(tool_name, args, schema.parameters as Record<string, unknown>);
    if (validationError) {
      return { tool_call_id, status: "error", result: validationError };
    }
  }
```

- [ ] **Step 3: Run existing executor tests + full suite**

Run: `cd apps/animus && bun test src/tools/executor.test.ts`
Expected: All existing tests PASS (validation shouldn't affect well-formed tool calls)

Run: `cd apps/animus && bun test`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add apps/animus/src/tools/registry.ts apps/animus/src/tools/executor.ts
git commit -m "feat(animus): wire arg validation into tool executor"
```

---

### Task 7: Edit Hints — Tests

**Files:**
- Create: `apps/animus/src/tools/edit_hints.test.ts`

- [ ] **Step 1: Write edit hint tests**

```typescript
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/animus && bun test src/tools/edit_hints.test.ts`
Expected: FAIL — `Cannot find module "./edit_hints"`

- [ ] **Step 3: Commit**

```bash
git add apps/animus/src/tools/edit_hints.test.ts
git commit -m "test: add edit hint diagnostic tests"
```

---

### Task 8: Edit Hints — Implementation

**Files:**
- Create: `apps/animus/src/tools/edit_hints.ts`

- [ ] **Step 1: Implement edit hints module**

```typescript
// apps/animus/src/tools/edit_hints.ts

/** Normalize \r\n to \n. Apply to both file content and search strings before matching. */
export function normalizeLineEndings(s: string): string {
  return s.replace(/\r\n/g, "\n");
}

/**
 * Fix common LLM over-escaping: \\n → \n, \\t → \t, etc.
 * Conservative — only handles patterns LLMs commonly produce.
 */
export function unescapeOverEscaped(s: string): string {
  return s
    .replace(/\\\\(?=[ntrfv'"`])/g, "__BACKSLASH_ESCAPE__")  // protect real \\n
    .replace(/\\n/g, "\n")
    .replace(/\\t/g, "\t")
    .replace(/\\r/g, "\r")
    .replace(/\\"/g, '"')
    .replace(/\\'/g, "'")
    .replace(/\\`/g, "`")
    .replace(/__BACKSLASH_ESCAPE__/g, "\\");
}

/**
 * Build a diagnostic error message when old_string isn't found in a file.
 * Checks for common causes in order: smart quotes, whitespace mismatch, then fallback.
 */
export function buildNotFoundError(
  filePath: string,
  oldString: string,
  fileContent: string,
): string {
  // 1. Smart quote mismatch
  if (hasSmartQuoteMismatch(oldString, fileContent)) {
    return `old_string not found in ${filePath}. The file uses smart/curly quotes but old_string has straight quotes. Re-read the file and copy the exact characters.`;
  }

  // 2. Whitespace mismatch
  if (hasWhitespaceMismatch(oldString, fileContent)) {
    return `old_string not found in ${filePath}. Found a near-match with different whitespace or indentation. Re-read the file for exact content.`;
  }

  // 3. Fallback
  return `old_string not found in ${filePath}. The file may have changed — re-read it and try again.`;
}

/** Check if replacing straight quotes with curly equivalents produces a match. */
function hasSmartQuoteMismatch(search: string, content: string): boolean {
  // Only check if search contains straight quotes
  if (!/['"]/.test(search)) return false;

  // Try common curly quote substitutions
  const variants = [
    search.replace(/'/g, "\u2019").replace(/"/g, "\u201D"),
    search.replace(/'/g, "\u2018").replace(/"/g, "\u201C"),
    search.replace(/'/g, "\u2019"),
    search.replace(/"/g, "\u201C"),
    search.replace(/"/g, "\u201D"),
  ];
  return variants.some((v) => content.includes(v));
}

/** Collapse all whitespace runs to single space, then compare. */
function hasWhitespaceMismatch(search: string, content: string): boolean {
  const collapse = (s: string) => s.replace(/\s+/g, " ").trim();
  const collapsedSearch = collapse(search);
  if (collapsedSearch.length < 10) return false; // too short to be meaningful
  return collapse(content).includes(collapsedSearch);
}
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cd apps/animus && bun test src/tools/edit_hints.test.ts`
Expected: All 10 tests PASS

- [ ] **Step 3: Commit**

```bash
git add apps/animus/src/tools/edit_hints.ts
git commit -m "feat(animus): add smart edit error diagnostics"
```

---

### Task 9: Wire Edit Hints into edit.ts

**Files:**
- Modify: `apps/animus/src/tools/edit.ts`

- [ ] **Step 1: Update edit.ts with normalization, over-escape auto-fix, and diagnostic hints**

Replace the entire contents of `apps/animus/src/tools/edit.ts` with:

```typescript
// apps/animus/src/tools/edit.ts
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import {
  normalizeLineEndings,
  unescapeOverEscaped,
  buildNotFoundError,
} from "./edit_hints";

export interface EditArgs {
  file_path: string;
  old_string: string;
  new_string: string;
}

export function executeEdit(args: EditArgs): {
  status: "success" | "error";
  result: string;
} {
  const { file_path, new_string } = args;
  if (!existsSync(file_path)) {
    return { status: "error", result: `File not found: ${file_path}` };
  }

  // Normalize line endings for cross-platform compatibility
  const content = normalizeLineEndings(readFileSync(file_path, "utf-8"));
  let old_string = normalizeLineEndings(args.old_string);

  // Check for ambiguous matches first
  const occurrences = content.split(old_string).length - 1;
  if (occurrences > 1) {
    return {
      status: "error",
      result: `old_string matches ${occurrences} locations in ${file_path}. Provide more context to disambiguate.`,
    };
  }

  // Exact match — apply it
  if (occurrences === 1) {
    const updated = content.replace(old_string, () => new_string);
    writeFileSync(file_path, updated, "utf-8");
    return { status: "success", result: `Edited ${file_path}` };
  }

  // Not found — try over-escape auto-fix
  const unescaped = unescapeOverEscaped(old_string);
  if (unescaped !== old_string && content.includes(unescaped)) {
    const unescapedOccurrences = content.split(unescaped).length - 1;
    if (unescapedOccurrences === 1) {
      const updated = content.replace(unescaped, () => new_string);
      writeFileSync(file_path, updated, "utf-8");
      return { status: "success", result: `Edited ${file_path}` };
    }
    if (unescapedOccurrences > 1) {
      return {
        status: "error",
        result: `old_string (after fixing escaping) matches ${unescapedOccurrences} locations in ${file_path}. Provide more context to disambiguate.`,
      };
    }
  }

  // Still not found — return diagnostic hint
  return { status: "error", result: buildNotFoundError(file_path, old_string, content) };
}
```

- [ ] **Step 2: Run existing edit tests**

Run: `cd apps/animus && bun test src/tools/edit.test.ts`
Expected: All 6 existing tests PASS

- [ ] **Step 3: Add integration tests for new behavior to edit.test.ts**

Append to the end of the `describe("edit tool", ...)` block in `apps/animus/src/tools/edit.test.ts`:

```typescript
  test("auto-fixes over-escaped newlines from LLM", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "line1\nline2\nline3", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "line1\\nline2",  // LLM sent \\n instead of \n
      new_string: "replaced",
    });

    expect(result.status).toBe("success");
    expect(readFileSync(filePath, "utf-8")).toBe("replaced\nline3");
  });

  test("normalizes \\r\\n line endings", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "line1\r\nline2\r\nline3", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "line1\nline2",  // Agent sends \n
      new_string: "replaced",
    });

    expect(result.status).toBe("success");
  });

  test("returns smart quote hint when applicable", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "it\u2019s a smart quote", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "it's a smart quote",  // straight quote
      new_string: "replaced",
    });

    expect(result.status).toBe("error");
    expect(result.result).toContain("smart");
  });

  test("returns whitespace hint when applicable", () => {
    const filePath = join(tempDir, "test.txt");
    writeFileSync(filePath, "  function  foo()  {\n    return 1;\n  }", "utf-8");

    const result = executeEdit({
      file_path: filePath,
      old_string: "function foo() {\n  return 1;\n}",
      new_string: "replaced",
    });

    expect(result.status).toBe("error");
    expect(result.result).toContain("whitespace");
  });
```

- [ ] **Step 4: Run all edit tests**

Run: `cd apps/animus && bun test src/tools/edit.test.ts`
Expected: All 10 tests PASS (6 existing + 4 new)

- [ ] **Step 5: Commit**

```bash
git add apps/animus/src/tools/edit.ts apps/animus/src/tools/edit.test.ts
git commit -m "feat(animus): wire smart edit hints into edit tool"
```

---

### Task 10: Wire Edit Hints into multi_edit.ts

**Files:**
- Modify: `apps/animus/src/tools/multi_edit.ts`

- [ ] **Step 1: Update multi_edit.ts with normalization, over-escape auto-fix, and diagnostic hints**

Replace the entire contents of `apps/animus/src/tools/multi_edit.ts` with:

```typescript
// apps/animus/src/tools/multi_edit.ts
import { readFileSync, writeFileSync, existsSync } from "node:fs";
import {
  normalizeLineEndings,
  unescapeOverEscaped,
  buildNotFoundError,
} from "./edit_hints";

export interface MultiEditArgs {
  file_path: string;
  edits: Array<{ old_string: string; new_string: string }>;
}

/**
 * Apply multiple edits to a single file atomically.
 * All edits are validated first; if any old_string is missing the whole
 * batch is rejected so the file is never left half-edited.
 */
export function executeMultiEdit(args: MultiEditArgs): {
  status: "success" | "error";
  result: string;
} {
  const { file_path, edits } = args;

  if (!existsSync(file_path)) {
    return { status: "error", result: `File not found: ${file_path}` };
  }
  if (!edits || edits.length === 0) {
    return { status: "error", result: "No edits provided" };
  }

  const original = normalizeLineEndings(readFileSync(file_path, "utf-8"));

  // Dry-run: apply edits sequentially to a copy to validate all old_strings
  // exist at the point they'll be applied (later edits see earlier results).
  let dryRun = original;
  for (let i = 0; i < edits.length; i++) {
    let old_string = normalizeLineEndings(edits[i].old_string);
    const new_string = edits[i].new_string;

    // Try over-escape auto-fix if not found
    if (!dryRun.includes(old_string)) {
      const unescaped = unescapeOverEscaped(old_string);
      if (unescaped !== old_string && dryRun.includes(unescaped)) {
        old_string = unescaped;
      } else {
        return {
          status: "error",
          result: `Edit ${i + 1}/${edits.length}: ${buildNotFoundError(file_path, old_string, dryRun)}`,
        };
      }
    }

    const occurrences = dryRun.split(old_string).length - 1;
    if (occurrences > 1) {
      return {
        status: "error",
        result: `Edit ${i + 1}/${edits.length}: old_string matches ${occurrences} locations in ${file_path}. Provide more context to disambiguate.`,
      };
    }

    // Use function replacer to avoid $-pattern interpretation
    dryRun = dryRun.replace(old_string, () => new_string);
  }

  // Dry-run succeeded — write the result
  writeFileSync(file_path, dryRun, "utf-8");
  return {
    status: "success",
    result: `Applied ${edits.length} edit(s) to ${file_path}`,
  };
}
```

- [ ] **Step 2: Run existing multi_edit tests**

Run: `cd apps/animus && bun test src/tools/multi_edit.test.ts`
Expected: All existing tests PASS

- [ ] **Step 3: Commit**

```bash
git add apps/animus/src/tools/multi_edit.ts
git commit -m "feat(animus): wire smart edit hints into multi_edit tool"
```

---

### Task 11: Full Test Suite Verification

**Files:** None (verification only)

- [ ] **Step 1: Run entire animus test suite**

Run: `cd apps/animus && bun test`
Expected: All tests PASS, no regressions

- [ ] **Step 2: Type-check**

Run: `cd apps/animus && npx tsc --noEmit`
Expected: No type errors

- [ ] **Step 3: Commit cleanup edits (if any)**

Only commit if fixes were needed. Otherwise skip.
