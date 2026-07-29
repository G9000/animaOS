import { describe, expect, test } from "bun:test";
import { createApiClient, type CoreFSSecurityStatus } from "@anima/api-client";
import { catalogNavigationAvailable } from "../src/context/CoreFSReadinessContext";

const status: CoreFSSecurityStatus = {
  coreId: "core-test",
  readiness: {
    state: "catalog_ready_degraded",
    catalogGeneration: 7,
    processedObjects: 0,
    capabilities: ["navigation", "exact_search"],
    retryable: true,
    families: {
      notes: { total: 3, processed: 0, failed: 1, degraded: true },
    },
  },
  rotation: {
    activeFrkVersion: 1,
    pendingFrkVersion: null,
    decryptOnlyFrkVersions: [],
    phase: "idle",
    blindIndexGeneration: null,
    blindIndexPendingGeneration: 2,
    blindIndexProgress: 1,
  },
};

describe("CoreFS readiness", () => {
  test("loads authenticated private-text-free status", async () => {
    const requests: Array<{ url: string; headers: Headers }> = [];
    const client = createApiClient({
      baseUrl: "http://anima.test/api",
      getUnlockToken: () => "unlock-token",
      fetchImpl: async (input, init) => {
        requests.push({
          url: String(input),
          headers: new Headers(init?.headers),
        });
        return new Response(JSON.stringify(status), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      },
    });

    const response = await client.corefs.securityStatus();

    expect(response).toEqual(status);
    expect(requests[0]?.url).toBe(
      "http://anima.test/api/corefs/security/status",
    );
    expect(requests[0]?.headers.get("x-anima-unlock")).toBe("unlock-token");
    expect(JSON.stringify(response)).not.toContain("private");
  });

  test("allows navigation as soon as catalog capability is published", () => {
    expect(catalogNavigationAvailable(status)).toBe(true);
    expect(
      catalogNavigationAvailable({
        ...status,
        readiness: {
          ...status.readiness,
          state: "text_indexing",
          capabilities: ["navigation", "exact_search", "text_search"],
        },
      }),
    ).toBe(true);
    expect(
      catalogNavigationAvailable({
        ...status,
        readiness: {
          ...status.readiness,
          state: "validating_core",
          capabilities: [],
        },
      }),
    ).toBe(false);
  });
});
