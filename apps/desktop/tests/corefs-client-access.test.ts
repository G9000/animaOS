import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  clientScopeDescription,
  requiresGrantConfirmation,
} from "../src/lib/corefsAccess";

describe("CoreFS client access settings", () => {
  test("requires explicit confirmation only for scope expansion", () => {
    expect(requiresGrantConfirmation("none", "read")).toBe(true);
    expect(requiresGrantConfirmation("read", "write")).toBe(true);
    expect(requiresGrantConfirmation("write", "manage")).toBe(true);
    expect(requiresGrantConfirmation("manage", "read")).toBe(false);
    expect(requiresGrantConfirmation("read", "none")).toBe(false);
    expect(clientScopeDescription("manage")).toContain("trash");
  });

  test("shows verified identity, device-local transfer state, stable IDs, and audit metadata", () => {
    const source = readFileSync(
      join(import.meta.dir, "../src/pages/settings/CoreFSAccessSettings.tsx"),
      "utf8",
    );
    expect(source).toContain("Moving this Core never transfers executable access");
    expect(source).toContain("reapprovalRequiredAfterTransfer");
    expect(source).toContain("installation.publisher.identity");
    expect(source).toContain("installation.installDigest");
    expect(source).toContain("stable ID");
    expect(source).toContain("installation.lastUsedAt");
    expect(source).toContain("Confirm grant");
    expect(source).toContain("Revoke installation");
  });
});
