# Animus Tool Improvements: Shell Launcher, Arg Validation, Smart Edit Errors

**Date:** 2026-03-29
**Scope:** `apps/animus/src/tools/`
**Reference:** Patterns adapted from `/Users/julio/letta-code/src/tools/impl/`

## Overview

Three independent improvements to animus tool infrastructure: cross-platform shell launching, pre-dispatch argument type validation, and intelligent edit error diagnostics. Each is a standalone module that slots into the existing architecture.

---

## 1. Shell Launcher (`src/tools/shell.ts`)

### Purpose

Replace hardcoded `["bash", "-c"]` in `bash.ts` and `process_manager.ts` with a platform-aware launcher that works on macOS, Linux, and Windows.

### Interface

```typescript
export type ShellLauncher = [executable: string, ...flags: string[]];
export function getShellLauncher(): ShellLauncher;
```

Returns a cached launcher array suitable for `Bun.spawn([...launcher, command])`.

### Platform Logic

**macOS (`darwin`):**
1. `/bin/zsh` with `-lc` (login shell — loads user PATH; avoids bash 3.2 HEREDOC apostrophe bug)
2. `/bin/bash` with `-c`

**Linux:**
1. `$SHELL` (if set and exists) with `-c` (or `-lc` for bash/zsh)
2. `/bin/bash -c`
3. `/usr/bin/bash -c`
4. `/bin/zsh -c`
5. `/bin/sh -c`

**Windows (`win32`):**
1. `powershell.exe` with `-NoProfile -Command`
2. `pwsh` with `-NoProfile -Command`
3. `cmd.exe` with `/d /s /c` (respects `ComSpec` env var)

### Launcher Probing

On first call, iterate through the platform's launcher list. For each candidate:
- Check if the executable exists (via `Bun.which()` or `existsSync()` for absolute paths)
- Cache the first valid launcher for the process lifetime
- If no launcher found, throw with a clear message listing what was tried

### Consumers

- `bash.ts`: Replace `Bun.spawn(["bash", "-c", command], ...)` with `Bun.spawn([...getShellLauncher(), command], ...)`
- `process_manager.ts`: Same replacement in `executeBgStart()`

### Shell Command Flag Helper

```typescript
function shellCommandFlag(shell: string, login: boolean): string[];
```

Returns `-lc` for bash/zsh when `login` is true, `-c` otherwise. For PowerShell returns `-NoProfile -Command`. For cmd returns `/d /s /c`.

---

## 2. Tool Arg Validation (`src/tools/validation.ts`)

### Purpose

Validate tool arguments against their JSON schema before execution. Catches type mismatches early with clear error messages instead of letting tools fail with cryptic runtime errors.

### Interface

```typescript
export function validateArgs(
  toolName: string,
  args: Record<string, unknown>,
  schema: Record<string, unknown>,
): string | null;  // null = valid, string = error message
```

### Validation Checks

1. **Required params**: Check `schema.required` array. Error: `"edit_file: missing required parameter 'file_path'"`
2. **Type matching**: For each provided arg, compare against `schema.properties[key].type`. Error: `"bash: 'command' must be a string, received number"`
3. **Array element types**: If schema specifies `items.type`, validate each element. Error: `"multi_edit: 'edits[2]' must be an object, received string"`
4. **Integer vs number**: Use `Number.isInteger()` to distinguish when schema expects `"integer"`.

### Type Mapping

```typescript
function jsonSchemaType(value: unknown): string;
// null → "null", Array → "array", true/false → "boolean",
// 42 → "integer", 3.14 → "number", "hi" → "string", {} → "object"
```

### Wiring

In `executor.ts`, between the permission check and the `switch` block:

```typescript
import { validateArgs } from "./validation";
import { TOOL_SCHEMA_MAP } from "./registry";

// ... inside executeTool():
const schema = TOOL_SCHEMA_MAP.get(tool_name);
if (schema) {
  const error = validateArgs(tool_name, args, schema.parameters);
  if (error) {
    return { tool_call_id, status: "error", result: error };
  }
}
```

`registry.ts` exports a `TOOL_SCHEMA_MAP: Map<string, ToolSchema>` built from the existing `ACTION_TOOL_SCHEMAS` array.

---

## 3. Smart Edit Error Hints (in `edit.ts`)

### Purpose

When `old_string` isn't found, diagnose *why* and either auto-fix the issue or return an actionable hint. Reduces wasted agent turns from stale or malformed edit strings.

### Always-On Normalization

Before any matching, normalize line endings: replace `\r\n` with `\n` in both the file content and the search string. This prevents cross-platform line ending mismatches silently.

### Auto-Fix: Over-Escaped Strings

Before reporting failure, run `unescapeOverEscaped(old_string)` and retry the match. If the unescaped version matches, use it silently — the edit succeeds without the agent needing to retry.

```typescript
function unescapeOverEscaped(s: string): string;
// \\n → \n, \\t → \t, \\" → ", \\' → ', \\\\ → \\, \\` → `
```

Conservative: only unescape the common LLM over-escape patterns. If the unescaped string differs from the original and matches, apply it.

### Diagnostic Hints (when match still fails)

`buildNotFoundError(filePath, oldString, fileContent)` returns a descriptive error by checking in order:

1. **Smart quote mismatch**: Check if replacing straight quotes (`'`, `"`) with their Unicode curly equivalents (`\u2018`/`\u2019`, `\u201C`/`\u201D`) produces a match. Hint: `"The file uses smart/curly quotes but old_string has straight quotes."`

2. **Whitespace mismatch**: Collapse all runs of whitespace in both strings to single spaces and compare. If they match: `"Found a near-match with different whitespace or indentation. Re-read the file for exact content."`

3. **Fallback**: `"old_string not found in <file_path>. The file may have changed — re-read it and try again."`

### Ambiguous Match Detection (separate from not-found)

Before attempting the replacement, count occurrences of `old_string` in the file. If > 1, return an error immediately — don't attempt the edit: `"old_string matches N locations in the file. Add more surrounding context to make it unique, or use multi_edit."` This check lives in `executeEdit()` directly, not inside `buildNotFoundError()`.

### Wiring

In `edit.ts`'s `executeEdit()`:
- Add `\r\n` → `\n` normalization at the top
- Count occurrences of `old_string` — if > 1, return ambiguity error
- On match failure (0 occurrences): try `unescapeOverEscaped()` → retry → if still fails, call `buildNotFoundError()`
- Export `buildNotFoundError` and `unescapeOverEscaped` so `multi_edit.ts` can reuse them

In `multi_edit.ts`'s `executeMultiEdit()`:
- Same normalization
- Same over-escape retry logic per edit
- Same `buildNotFoundError()` for the first failing edit in the dry-run phase

---

## Testing

### Shell Launcher Tests (`shell.test.ts`)
- Returns a valid launcher array on current platform
- Caching: second call returns same array (reference equality)
- Launcher array works with `Bun.spawn([...launcher, "echo hello"])`

### Validation Tests (`validation.test.ts`)
- Missing required param returns error string
- Wrong type returns error string with expected vs actual
- Correct args return null
- Array element type checking
- Optional params can be omitted
- Unknown params are ignored (forward-compat)

### Edit Hint Tests (added to existing `edit.test.ts` or new `edit_hints.test.ts`)
- Over-escaped `\\n` auto-fixes silently
- Smart quote mismatch produces hint
- Whitespace mismatch produces hint
- Ambiguous match (>1 occurrence) produces hint with count
- `\r\n` normalization allows match
- Fallback error when no hint applies

---

## Files Changed

| File | Change |
|------|--------|
| `src/tools/shell.ts` | **New** — platform-aware shell launcher |
| `src/tools/shell.test.ts` | **New** — launcher tests |
| `src/tools/validation.ts` | **New** — arg type validation |
| `src/tools/validation.test.ts` | **New** — validation tests |
| `src/tools/edit.ts` | **Modified** — add normalization, over-escape auto-fix, `buildNotFoundError()` |
| `src/tools/multi_edit.ts` | **Modified** — same edit improvements |
| `src/tools/edit_hints.test.ts` | **New** — edit diagnostic tests |
| `src/tools/executor.ts` | **Modified** — add `validateArgs()` call before dispatch |
| `src/tools/registry.ts` | **Modified** — export `TOOL_SCHEMA_MAP` alongside existing array |
| `src/tools/bash.ts` | **Modified** — use `getShellLauncher()` |
| `src/tools/process_manager.ts` | **Modified** — use `getShellLauncher()` |

---

## Non-Goals

- Model-specific tool variants (Codex/Gemini adapters)
- Subagent/task spawning
- Persistent memory system
- Interrupt queue / batch approval
- Binary file detection in read/edit (simple but separate concern)
