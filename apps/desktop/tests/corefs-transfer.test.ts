import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const desktopRoot = join(import.meta.dir, "..");

function source(path: string): string {
  return readFileSync(join(desktopRoot, path), "utf8");
}

describe("ANIMA CORE transfer settings", () => {
  test("makes full Core export primary and keeps scoped recovery explicit", () => {
    const page = source("src/pages/settings/CoreTransferSettings.tsx");

    expect(page).toContain("Export ANIMA CORE");
    expect(page).toContain("Restore ANIMA CORE");
    expect(page).toContain("Full ANIMA CORE");
    expect(page).toContain("Soul only");
    expect(page).toContain("CoreFS only");
    expect(page).toContain("filesystem-missing degraded mode");
    expect(page).toContain("V1 reattachment is not supported");
    expect(page).toContain("Runtime databases, device configuration");
    expect(page).not.toContain("drag-copy");
  });

  test("shows checkpoint, capacity, publication, verification, and safe cancellation state", () => {
    const page = source("src/pages/settings/CoreTransferSettings.tsx");

    for (const contract of [
      "Soul checkpoint",
      "Filesystem checkpoint",
      "Required free space",
      "Single-file limit",
      "publicationMode",
      "progressPercent",
      "Cancel safely",
      "Verified ANIMA CORE published safely",
      "unpublished partial output was removed",
      "Verify and stage restore",
      "Required staging",
      "running Core was not changed",
      "partial extraction was removed",
      "Activate on restart",
      "current Core remains active until shutdown",
      "Retained Core rollback",
      "Rollback on restart",
      "rollbackConfirmed",
      "activeCoreId",
      "retainedCoreId",
      "Browse recovered CoreFS",
      "browseCoreFsRecovery",
      "Credentials stay",
      "never attaches the filesystem to a Soul",
      "Secure recovered CoreFS",
      "replaceCoreFsRecoveryCredentials",
      "replacementConfirmed",
      "fresh FS-only password and recovery",
      "shown only in this response",
      "cannot attach a Soul or promote",
      "Re-export recovered CoreFS",
      "exportCoreFsRecovery",
      "exact staged generation",
      "never becomes the running Core",
      "Cancel recovery export safely",
      "Import a legacy vault",
      "encrypted V1 JSON vaults and ANMA capsules",
      "readLegacyVaultFile",
      "Import legacy source",
      "only before CoreFS migration begins",
      "CoreFS migration cutover",
      "Prepare migration",
      "Accept verified migration",
      "Reject and restore legacy",
      "stopped-Runtime recovery bundle",
      "single forward-only cutover event",
    ]) {
      expect(page).toContain(contract);
    }
    expect(page).not.toContain("localStorage");
    expect(page).not.toContain("Multipart export remains disabled");
    expect(page).not.toContain("multipart export, which is still gated");
  });

  test("redirects the legacy vault screen to the Core transfer flow", () => {
    const app = source("src/App.tsx");
    const settings = source("src/pages/settings/Settings.tsx");

    expect(app).toContain('path="core-transfer"');
    expect(app).toContain('path="vault" element={<Navigate to="../core-transfer" replace />}');
    expect(settings).toContain('to: "/settings/core-transfer"');
    expect(settings).toContain('label: "Core Transfer"');
  });
});
