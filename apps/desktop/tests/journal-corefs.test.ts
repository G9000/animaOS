import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "bun:test";

const journalSource = readFileSync(
  join(import.meta.dir, "..", "src", "pages", "Journal.tsx"),
  "utf8",
);

describe("Journal CoreFS cutover boundary", () => {
  test("does not delete legacy local drafts before verified CoreFS cutover", () => {
    expect(journalSource).not.toContain("anima:diary:draft:");
    expect(journalSource).not.toContain("window.localStorage.removeItem");
  });
});
