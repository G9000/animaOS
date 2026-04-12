import { afterEach, describe, expect, test } from "bun:test";

import { getModClient, resetModClient } from "../src/lib/mod-client";

const originalFetch = globalThis.fetch;

describe("mod client", () => {
  afterEach(() => {
    globalThis.fetch = originalFetch;
    resetModClient();
  });

  test("surfaces non-JSON error bodies instead of throwing JSON parse errors", async () => {
    globalThis.fetch = (async () =>
      new Response("upstream failed", {
        status: 502,
        headers: { "Content-Type": "text/plain" },
      })) as typeof fetch;

    await expect(getModClient().listMods()).rejects.toThrow("upstream failed");
  });
});
