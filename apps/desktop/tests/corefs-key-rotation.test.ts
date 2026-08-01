import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createApiClient } from "@anima/api-client";

describe("CoreFS key rotation", () => {
  test("sends credentials only in the authenticated request body", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const client = createApiClient({
      baseUrl: "http://anima.test/api",
      getUnlockToken: () => "unlock-token",
      fetchImpl: async (input, init) => {
        requests.push({ url: String(input), init });
        return new Response(
          JSON.stringify({
            success: true,
            unlockToken: "replacement-token",
            activeFrkVersion: 2,
            committedCatalogGeneration: 9,
            resumed: false,
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      },
    });

    const response = await client.corefs.rotateRootKey(
      "current-password",
      "recovery phrase words",
    );

    expect(response.unlockToken).toBe("replacement-token");
    expect(requests[0]?.url).toBe(
      "http://anima.test/api/corefs/security/rotate",
    );
    expect(JSON.parse(String(requests[0]?.init?.body))).toEqual({
      currentPassword: "current-password",
      recoveryPhrase: "recovery phrase words",
    });
    expect(new Headers(requests[0]?.init?.headers).get("x-anima-unlock")).toBe(
      "unlock-token",
    );
    expect(requests[0]?.url).not.toContain("current-password");
    expect(requests[0]?.url).not.toContain("recovery");
  });

  test("shows reopen verification and old-key retirement gates", () => {
    const settings = readFileSync(
      join(import.meta.dir, "..", "src", "pages", "settings", "SecuritySettings.tsx"),
      "utf8",
    );

    expect(settings).toContain("passwordReopenVerified");
    expect(settings).toContain("recoveryReopenVerified");
    expect(settings).toContain("oldKeyRetirementSafe");
    expect(settings).toContain("oldKeyRetirementBlockers");
  });
});
