import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createApiClient } from "../src/client";

function readClientSource(): string {
  return readFileSync(join(import.meta.dir, "../src/client.ts"), "utf8");
}

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

  test("serializes optional today context", async () => {
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

    await api.chat.send("Help me focus.", 7, undefined, [], [], {
      date: "2026-05-30",
      mood: "tired",
      energy: "low",
      note: "short replies",
    });

    expect(requestBody).toEqual({
      message: "Help me focus.",
      userId: 7,
      stream: false,
      todayContext: {
        date: "2026-05-30",
        mood: "tired",
        energy: "low",
        note: "short replies",
      },
    });
  });

  test("serializes selected document ids in chat requests", async () => {
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

    await api.chat.send("Use the manual.", 7, undefined, [], [], null, [4, 9]);

    expect(requestBody).toEqual({
      message: "Use the manual.",
      userId: 7,
      stream: false,
      documentIds: [4, 9],
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

  test("calls image deletion and retention endpoints", async () => {
    const requests: Array<{ url: string; method: string; body?: unknown }> = [];
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (input, init) => {
        requests.push({
          url: String(input),
          method: init?.method || "GET",
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        return new Response(JSON.stringify({ status: "ok" }));
      },
    });

    await api.images.removeFromMessage(12, "img_abc");
    await api.images.forget(34);
    await api.images.setRetention(34, "retained");

    expect(requests).toEqual([
      {
        url: "https://api.test/api/images/messages/12/attachments/img_abc",
        method: "DELETE",
        body: undefined,
      },
      {
        url: "https://api.test/api/images/34",
        method: "DELETE",
        body: undefined,
      },
      {
        url: "https://api.test/api/images/34/retention",
        method: "PATCH",
        body: { retentionState: "retained" },
      },
    ]);
  });

  test("gets and updates presence configuration", async () => {
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

    await api.presence.get(7);
    await api.presence.update(7, {
      mainChatEnabled: false,
      checkInNudgesEnabled: false,
      customInstruction: "mention Tappy",
    });

    expect(requests).toEqual([
      {
        url: "https://api.test/api/presence/7",
        method: "GET",
      },
      {
        url: "https://api.test/api/presence/7",
        method: "PUT",
        body: {
          mainChatEnabled: false,
          checkInNudgesEnabled: false,
          customInstruction: "mention Tappy",
        },
      },
    ]);
  });

  test("requests grounded agent state", async () => {
    let requestedUrl = "";
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (input) => {
        requestedUrl = String(input);
        return new Response(
          JSON.stringify({
            userId: 7,
            dominantEmotion: "curious",
            thought: "Tracking the agent state line handoff.",
            thoughtSource: "working_memory",
            chatPrompt: "What's behind that thought?",
            contextMessages: [
              {
                role: "assistant",
                content:
                  "Current companion state: Tracking the agent state line handoff. Recent emotion: curious.",
                source: "agent_state",
              },
            ],
          }),
        );
      },
    });

    const state = await api.consciousness.getAgentState(7);

    expect(requestedUrl).toBe(
      "https://api.test/api/consciousness/7/agent-state",
    );
    expect(state.thought).toBe("Tracking the agent state line handoff.");
    expect(state.contextMessages[0]?.source).toBe("agent_state");
  });

  test("requests compiled agent biography preview", async () => {
    let requestedUrl = "";
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (input) => {
        requestedUrl = String(input);
        return new Response(
          JSON.stringify({
            userId: 7,
            agentName: "Anima",
            relationship: "companion",
            agentType: "mirror",
            avatarUrl: null,
            agentBirthday: "2026-06-24T19:05:54+00:00",
            birthday: "1995-05-23",
            dominantEmotion: "curious",
            identityDraft: "Identity",
            personaDraft: "Persona",
            biography: "Identity\n\nPersona",
            contextLine: "Holding settings context.",
            sections: [
              {
                id: "identity",
                title: "Core Identity",
                content: "Identity",
                source: "self_identity",
              },
            ],
            promptBlockLabels: ["self_identity"],
          }),
        );
      },
    });

    const preview = await api.consciousness.getAgentBiographyPreview(7);

    expect(requestedUrl).toBe(
      "https://api.test/api/consciousness/7/agent-biography-preview",
    );
    expect(preview.agentName).toBe("Anima");
    expect(preview.agentBirthday).toBe("2026-06-24T19:05:54+00:00");
    expect(preview.sections[0]?.source).toBe("self_identity");
  });

  test("serializes identity override for protected self-model updates", async () => {
    let requestBody: unknown = null;
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (_input, init) => {
        requestBody = JSON.parse(String(init?.body));
        return new Response(
          JSON.stringify({
            section: "user_directive",
            content: "Protect identity changes.",
            version: 2,
            updatedBy: "user_edit",
            updatedAt: null,
          }),
        );
      },
    });

    await api.consciousness.updateSelfModelSection(
      7,
      "user_directive",
      "Protect identity changes.",
      { allowIdentityOverride: true },
    );

    expect(requestBody).toEqual({
      content: "Protect identity changes.",
      allowIdentityOverride: true,
    });
  });

  test("serializes agent birthday override for profile updates", async () => {
    let requestBody: unknown = null;
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (_input, init) => {
        requestBody = JSON.parse(String(init?.body));
        return new Response(
          JSON.stringify({
            agentName: "Anima",
            relationship: "companion",
            personaTemplate: "default",
            agentType: "mirror",
            avatarUrl: null,
            agentBirthday: "2026-06-24T19:05:54",
            setupComplete: true,
          }),
        );
      },
    });

    await api.consciousness.updateAgentProfile(7, {
      agentBirthday: "2026-06-24T19:05:54",
      allowIdentityOverride: true,
    });

    expect(requestBody).toEqual({
      agentBirthday: "2026-06-24T19:05:54",
      allowIdentityOverride: true,
    });
  });

  test("serializes thinking monologue lines for profile updates", async () => {
    let requestBody: unknown = null;
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (_input, init) => {
        requestBody = JSON.parse(String(init?.body));
        return new Response(
          JSON.stringify({
            agentName: "Anima",
            relationship: "companion",
            personaTemplate: "default",
            agentType: "companion",
            avatarUrl: null,
            agentBirthday: "2026-06-24T19:05:54",
            thinkingMonologue: ["I am holding the context together."],
            setupComplete: true,
          }),
        );
      },
    });

    await api.consciousness.updateAgentProfile(7, {
      thinkingMonologue: ["I am holding the context together."],
    });

    expect(requestBody).toEqual({
      thinkingMonologue: ["I am holding the context together."],
    });
  });

  test("requests generated thinking monologue draft", async () => {
    let requestedUrl = "";
    let requestedMethod = "";
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (input, init) => {
        requestedUrl = String(input);
        requestedMethod = init?.method || "GET";
        return new Response(
          JSON.stringify({
            thinkingMonologue: [
              "I am tracing the shape of this.",
              "Let me hold this carefully.",
            ],
          }),
        );
      },
    });

    const result = await api.consciousness.generateThinkingMonologue(7);

    expect(requestedUrl).toBe(
      "https://api.test/api/consciousness/7/agent-profile/thinking-monologue/generate",
    );
    expect(requestedMethod).toBe("POST");
    expect(result.thinkingMonologue).toEqual([
      "I am tracing the shape of this.",
      "Let me hold this carefully.",
    ]);
  });

  test("does not model agent type as an editable profile update field", () => {
    const source = readClientSource();

    expect(source).not.toMatch(/updateAgentProfile:[\s\S]*agentType\?: string;/);
  });

  test("requests diary entries and uploads diary attachments", async () => {
    const requests: Array<{ url: string; method: string; bodyType: string; body?: unknown }> = [];
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (input, init) => {
        const body = init?.body;
        requests.push({
          url: String(input),
          method: init?.method || "GET",
          bodyType: body instanceof FormData ? "form" : typeof body,
          body:
            typeof body === "string"
              ? JSON.parse(body)
              : body instanceof FormData
                ? {
                    caption: body.get("caption"),
                    fileName: (body.get("file") as File).name,
                  }
                : undefined,
        });
        return new Response(
          JSON.stringify({
            id: 12,
            userId: 7,
            entryDate: "2026-06-05",
            title: "Private",
            body: "Today mattered.",
            mood: "calm",
            source: "user",
            attachments: [],
            createdAt: null,
            updatedAt: null,
          }),
        );
      },
    });

    await api.diary.list(7, 25);
    await api.diary.create(7, {
      entryDate: "2026-06-05",
      title: "Private",
      body: "Today mattered.",
      mood: "calm",
    });
    await api.diary.uploadAttachment(
      12,
      new File(["voice"], "voice.wav", { type: "audio/wav" }),
      "Voice note",
    );
    await api.diary.delete(12);

    expect(requests).toEqual([
      {
        url: "https://api.test/api/diary?userId=7&limit=25",
        method: "GET",
        bodyType: "undefined",
      },
      {
        url: "https://api.test/api/diary",
        method: "POST",
        bodyType: "string",
        body: {
          userId: 7,
          entryDate: "2026-06-05",
          title: "Private",
          body: "Today mattered.",
          mood: "calm",
        },
      },
      {
        url: "https://api.test/api/diary/12/attachments",
        method: "POST",
        bodyType: "form",
        body: {
          caption: "Voice note",
          fileName: "voice.wav",
        },
      },
      {
        url: "https://api.test/api/diary/12",
        method: "DELETE",
        bodyType: "undefined",
      },
    ]);
  });

  test("uploads PDF documents and resumes workflows", async () => {
    const requests: Array<{ url: string; method: string; bodyType: string; body?: unknown }> = [];
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (input, init) => {
        const body = init?.body;
        requests.push({
          url: String(input),
          method: init?.method || "GET",
          bodyType: body instanceof FormData ? "form" : typeof body,
          body:
            body instanceof FormData
              ? {
                  userId: body.get("userId"),
                  threadId: body.get("threadId"),
                  fileName: (body.get("file") as File).name,
                }
              : typeof body === "string"
                ? JSON.parse(body)
                : undefined,
        });
        return new Response(
          JSON.stringify({
            workflowId: 22,
            status: "created",
            currentState: "created",
          }),
        );
      },
    });

    await api.documents.uploadPdf(
      7,
      new File(["%PDF-1.4"], "manual.pdf", { type: "application/pdf" }),
      3,
    );
    await api.documents.resumeWorkflow(22);

    expect(requests).toEqual([
      {
        url: "https://api.test/api/documents/pdf",
        method: "POST",
        bodyType: "form",
        body: {
          userId: "7",
          threadId: "3",
          fileName: "manual.pdf",
        },
      },
      {
        url: "https://api.test/api/documents/workflows/22/resume",
        method: "POST",
        bodyType: "undefined",
      },
    ]);
  });
});
