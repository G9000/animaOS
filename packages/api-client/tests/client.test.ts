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

  test("serializes optional chat context messages", async () => {
    let requestBody: unknown = null;
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (_input, init) => {
        requestBody = JSON.parse(String(init?.body));
        return new Response(
          JSON.stringify({
            response: "ok",
            model: "test",
            provider: "test",
            toolsUsed: [],
          }),
        );
      },
    });

    await api.chat.send("That sounds right.", 7, undefined, [], [
      {
        role: "assistant",
        content: "Hello there. I hope you and Tappy are having a peaceful start.",
        source: "home_greeting",
      },
    ]);

    expect(requestBody).toEqual({
      message: "That sounds right.",
      userId: 7,
      stream: false,
      contextMessages: [
        {
          role: "assistant",
          content: "Hello there. I hope you and Tappy are having a peaceful start.",
          source: "home_greeting",
        },
      ],
    });
  });

  test("requests proactive notices with optional custom instruction", async () => {
    let requestedUrl = "";
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (input) => {
        requestedUrl = String(input);
        return new Response(JSON.stringify({ notice: null }));
      },
    });

    await api.chat.proactiveNotice(7, "mention Tappy");

    expect(requestedUrl).toBe(
      "https://api.test/api/chat/proactive-notice?userId=7&instruction=mention+Tappy",
    );
  });

  test("gets and updates proactivity configuration", async () => {
    const requests: Array<{ url: string; method: string; body?: unknown }> = [];
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (input, init) => {
        requests.push({
          url: String(input),
          method: init?.method || "GET",
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        return new Response(
          JSON.stringify({
            userId: 7,
            enabled: true,
            mainChatEnabled: false,
            homeGreetingContextEnabled: true,
            taskNudgesEnabled: true,
            memoryNudgesEnabled: true,
            checkInNudgesEnabled: false,
            customInstruction: "mention Tappy",
          }),
        );
      },
    });

    await api.proactivity.get(7);
    await api.proactivity.update(7, {
      mainChatEnabled: false,
      checkInNudgesEnabled: false,
      customInstruction: "mention Tappy",
    });

    expect(requests).toEqual([
      {
        url: "https://api.test/api/proactivity/7",
        method: "GET",
      },
      {
        url: "https://api.test/api/proactivity/7",
        method: "PUT",
        body: {
          mainChatEnabled: false,
          checkInNudgesEnabled: false,
          customInstruction: "mention Tappy",
        },
      },
    ]);
  });
});
