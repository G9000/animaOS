/**
 * Google Module Tests
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import googleMod from "./mod.js";
import { createMockContext } from "../../tests/setup.js";

const originalFetch = globalThis.fetch;

function createGoogleContext() {
  return createMockContext({
    config: {
      clientId: "client-id",
      clientSecret: "client-secret",
    },
    store: {
      get: async (key: string) => {
        if (key === "google:tokens:1") {
          return {
            email: "user@example.com",
            accessToken: "access-token",
            refreshToken: "refresh-token",
            expiresAt: Date.now() + 60 * 60 * 1000,
          };
        }
        return null;
      },
      set: async () => {},
      delete: async () => {},
      has: async () => false,
    },
  });
}

describe("Google Module", () => {
  beforeEach(async () => {
    await googleMod.init(createGoogleContext());
  });

  afterEach(async () => {
    globalThis.fetch = originalFetch;
    await googleMod.stop?.();
  });

  it("accepts Gmail search requests without optional maxResults", async () => {
    const requestedUrls: string[] = [];
    globalThis.fetch = async (input) => {
      requestedUrls.push(String(input));
      return Response.json({ messages: [] });
    };

    const router = googleMod.getRouter?.();
    expect(router).toBeDefined();

    const response = await router!.handle(
      new Request("http://localhost/gmail/search", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ userId: 1, query: "from:friend@example.com" }),
      }),
    );
    const body = await response.json();

    expect(body.error).toBeUndefined();
    expect(body.result).toContain("No emails found");
    expect(requestedUrls[0]).toContain("maxResults=10");
  });

  it("accepts calendar list requests without optional maxResults", async () => {
    const requestedUrls: string[] = [];
    globalThis.fetch = async (input) => {
      requestedUrls.push(String(input));
      return Response.json({ items: [] });
    };

    const router = googleMod.getRouter?.();
    expect(router).toBeDefined();

    const response = await router!.handle(
      new Request("http://localhost/calendar/events", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          userId: 1,
          startDate: "2026-04-30",
          endDate: "2026-05-01",
        }),
      }),
    );
    const body = await response.json();

    expect(body.error).toBeUndefined();
    expect(body.result).toContain("No calendar events found");
    expect(requestedUrls[0]).toContain("maxResults=10");
  });
});
