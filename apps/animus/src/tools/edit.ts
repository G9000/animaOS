// apps/animus/src/tools/edit.ts
import { readFileSync, writeFileSync, existsSync } from "node:fs";

export interface EditArgs {
  file_path: string;
  old_string: string;
  new_string: string;
}

export function executeEdit(args: EditArgs): {
  status: "success" | "error";
  result: string;
} {
  const { file_path, old_string, new_string } = args;
  if (!existsSync(file_path)) {
    return { status: "error", result: `File not found: ${file_path}` };
  }
  const content = readFileSync(file_path, "utf-8");
  if (!content.includes(old_string)) {
    return { status: "error", result: `old_string not found in ${file_path}` };
  }
  // Count occurrences to warn on ambiguous matches
  const occurrences = content.split(old_string).length - 1;
  if (occurrences > 1) {
    return {
      status: "error",
      result: `old_string matches ${occurrences} locations in ${file_path}. Provide more context to disambiguate.`,
    };
  }
  // Use function replacer to avoid $-pattern interpretation in new_string
  const updated = content.replace(old_string, () => new_string);
  writeFileSync(file_path, updated, "utf-8");
  return { status: "success", result: `Edited ${file_path}` };
}
