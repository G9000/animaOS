// apps/animus/src/tools/multi_edit.ts
import { readFileSync, writeFileSync, existsSync } from "node:fs";

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

  const original = readFileSync(file_path, "utf-8");

  // Dry-run: apply edits sequentially to a copy to validate all old_strings
  // exist at the point they'll be applied (later edits see earlier results).
  let dryRun = original;
  for (let i = 0; i < edits.length; i++) {
    const { old_string } = edits[i];
    if (!dryRun.includes(old_string)) {
      return {
        status: "error",
        result: `Edit ${i + 1}/${edits.length}: old_string not found in ${file_path}`,
      };
    }
    dryRun = dryRun.replace(old_string, edits[i].new_string);
  }

  // Dry-run succeeded — write the result
  writeFileSync(file_path, dryRun, "utf-8");
  return {
    status: "success",
    result: `Applied ${edits.length} edit(s) to ${file_path}`,
  };
}
