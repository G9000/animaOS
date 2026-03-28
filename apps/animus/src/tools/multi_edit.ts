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
