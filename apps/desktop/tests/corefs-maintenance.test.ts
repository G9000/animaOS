import { describe, expect, test } from "bun:test";
import { canConfirmMaintenance, maintenanceProgress } from "../src/pages/settings/maintenanceReview";

describe("CoreFS maintenance review", () => {
  test("requires a verified backup, an explicit confirmation and an unexpired inventory", () => {
    const review = { backupVerified: true, observedAtMs: 1000, expiresInSeconds: 120, inventory: { blockers: [] } };
    expect(canConfirmMaintenance(review, false, 2000)).toBe(false);
    expect(canConfirmMaintenance(review, true, 2000)).toBe(true);
    expect(canConfirmMaintenance(review, true, 121001)).toBe(false);
    expect(canConfirmMaintenance({ ...review, backupVerified: false }, true, 2000)).toBe(false);
    expect(canConfirmMaintenance({ ...review, inventory: { blockers: ["active_transfer"] } }, true, 2000)).toBe(false);
  });

  test("allows only authenticated interrupted-operation continuation", () => {
    const review = {
      backupVerified: true, observedAtMs: 1000, expiresInSeconds: 120,
      parameters: { action: "resume" },
      inventory: {
        blockers: ["maintenance_operation_pending", "head_lineage_corrupt"],
        resumeAuthorized: true, cancelAuthorized: false,
        operation: { kind: "reanchor", expectedInventoryDigest: "a".repeat(64), sourceHeadCatalogHash: "b".repeat(64), sourceHeadGeneration: 2 },
      },
    };
    expect(canConfirmMaintenance(review, true, 2000)).toBe(true);
    expect(canConfirmMaintenance({ ...review, parameters: { action: "cancel" } }, true, 2000)).toBe(false);
    expect(canConfirmMaintenance({ ...review, parameters: { action: "cancel" }, inventory: { ...review.inventory, cancelAuthorized: true } }, true, 2000)).toBe(true);
    expect(canConfirmMaintenance({ ...review, parameters: { action: "prune" } }, true, 2000)).toBe(false);
    expect(canConfirmMaintenance({ ...review, inventory: { ...review.inventory, resumeAuthorized: false } }, true, 2000)).toBe(false);
    expect(canConfirmMaintenance({ ...review, inventory: { ...review.inventory, blockers: ["journal_corrupt"] } }, true, 2000)).toBe(false);
  });

  test("quarantine progress never claims content was erased", () => {
    expect(maintenanceProgress({ state: "quarantining", quarantinedCount: 12, sweptCount: 0, totalCount: 20 })).toContain("0 of 20 removed");
    expect(maintenanceProgress({ state: "completed", quarantinedCount: 20, sweptCount: 20, totalCount: 20 })).toContain("20 of 20 removed");
    expect(maintenanceProgress(null)).toBe("No maintenance operation is active.");
  });
});
