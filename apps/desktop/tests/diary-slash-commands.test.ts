import { describe, expect, test } from "bun:test";
import {
  SLASH_COMMANDS,
  filterSlashCommands,
} from "../src/features/diary/editor/slashCommands";

describe("slash command filtering", () => {
  test("returns every command for an empty query", () => {
    expect(filterSlashCommands(SLASH_COMMANDS, "")).toHaveLength(SLASH_COMMANDS.length);
  });

  test("matches on label case-insensitively", () => {
    const ids = filterSlashCommands(SLASH_COMMANDS, "HEAD").map((c) => c.id);
    expect(ids).toContain("h1");
    expect(ids).not.toContain("divider");
  });

  test("matches on the markdown hint so '1.' finds the numbered list", () => {
    expect(filterSlashCommands(SLASH_COMMANDS, "1.").map((c) => c.id)).toContain("ordered");
  });

  test("returns nothing for an unmatched query", () => {
    expect(filterSlashCommands(SLASH_COMMANDS, "zzzz")).toHaveLength(0);
  });

  test("exposes the block types the diary supports", () => {
    const ids = SLASH_COMMANDS.map((c) => c.id);
    for (const id of ["h1", "h2", "h3", "bullet", "ordered", "task", "quote", "code", "divider", "table", "toggle", "callout", "image"]) {
      expect(ids).toContain(id);
    }
  });
});
