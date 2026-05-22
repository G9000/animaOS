import { describe, expect, test } from "bun:test";

import { createApiClient } from "../src/client";

describe("createApiClient error handling", () => {
  test("surfaces normalized validation details arrays", async () => {
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async () =>
        new Response(
          JSON.stringify({
            details: [
              { msg: "Declared MIME type does not match image bytes." },
              { msg: "Attach at most 4 images per message." },
            ],
          }),
          { status: 422, statusText: "Unprocessable Entity" },
        ),
    });

    await expect(api.chat.send("describe this", 7)).rejects.toThrow(
      "Declared MIME type does not match image bytes.; Attach at most 4 images per message.",
    );
  });
});
