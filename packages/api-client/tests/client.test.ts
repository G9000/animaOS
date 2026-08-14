import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { createApiClient } from "../src/client";

function readClientSource(): string {
  return readFileSync(join(import.meta.dir, "../src/client.ts"), "utf8");
}

describe("createApiClient error handling", () => {
  test("returns restart metadata for whole-Core account deletion", async () => {
    let requestedUrl = "";
    let requestedMethod = "";
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (input, init) => {
        requestedUrl = String(input);
        requestedMethod = init?.method || "GET";
        return new Response(
          JSON.stringify({
            message: "Whole-Core account deletion scheduled for restart",
            restartRequired: true,
            deletionId: "d0f7d7e7-9c57-4cd5-9942-f38aa8b1475a",
          }),
        );
      },
    });

    const result = await api.users.delete(7);

    expect(requestedUrl).toBe("https://api.test/api/users/7");
    expect(requestedMethod).toBe("DELETE");
    expect(result.restartRequired).toBe(true);
    expect(result.deletionId).toBe("d0f7d7e7-9c57-4cd5-9942-f38aa8b1475a");
  });

  test("serializes reversible Core migration decisions", async () => {
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
            state: "awaiting_acceptance",
            generation: 9,
            migratedCount: 41,
            errorCode: null,
            restartRequired: false,
            firstWriteReady: false,
            forwardOnly: false,
          }),
        );
      },
    });

    await api.corefs.transfer.migrationStatus();
    await api.corefs.transfer.runMigration(true);
    await api.corefs.transfer.acceptMigration();
    await api.corefs.transfer.rejectMigration();

    expect(requests).toEqual([
      {
        url: "https://api.test/api/corefs/transfer/migration/status",
        method: "GET",
        body: undefined,
      },
      {
        url: "https://api.test/api/corefs/transfer/migration/run",
        method: "POST",
        body: { retryFailed: true },
      },
      {
        url: "https://api.test/api/corefs/transfer/migration/accept",
        method: "POST",
        body: { confirmed: true },
      },
      {
        url: "https://api.test/api/corefs/transfer/migration/reject",
        method: "POST",
        body: { confirmed: true },
      },
    ]);
  });

  test("sends the immutable legacy-draft handoff token", async () => {
    let requestBody: unknown = null;
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async (_input, init) => {
        requestBody = JSON.parse(String(init?.body));
        return new Response(
          JSON.stringify({
            stableId: "01J00000000000000000000000",
            revision: 1,
            generation: 1,
            catalogHash: "a".repeat(64),
            verified: true,
            authoritative: false,
            completionToken: {
              draftId: "legacy-key",
              clientRevision: 4,
              contentSha256: "b".repeat(64),
            },
          }),
        );
      },
    });

    const result = await api.diary.importLegacyDraft(7, {
      draftId: "legacy-key",
      clientRevision: 4,
      contentSha256: "b".repeat(64),
      html: "<p>private</p>",
      title: "Private",
      mood: "calm",
      entryDate: "2026-08-12",
      updatedAt: "2026-08-12T12:00:00Z",
    });

    expect(requestBody).toEqual({
      userId: 7,
      draftId: "legacy-key",
      clientRevision: 4,
      contentSha256: "b".repeat(64),
      html: "<p>private</p>",
      title: "Private",
      mood: "calm",
      entryDate: "2026-08-12",
      updatedAt: "2026-08-12T12:00:00Z",
    });
    expect(result.completionToken.clientRevision).toBe(4);
  });

  test("serializes CoreFS operations without caller-selected identity headers", async () => {
    let requestedUrl = "";
    let requestBody: unknown = null;
    let requestHeaders: Headers | null = null;
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      getUnlockToken: () => "unlock-token",
      getNonce: () => "sidecar-nonce",
      fetchImpl: async (input, init) => {
        requestedUrl = String(input);
        requestBody = JSON.parse(String(init?.body));
        requestHeaders = new Headers(init?.headers);
        return new Response(
          JSON.stringify({
            principal: {
              kind: "user",
              id: "42",
              userId: 42,
            },
            operation: "stat",
            selected: { generation: 3, catalogHash: "hash" },
            result: { version: "corefs-logical-v1", result: {} },
          }),
        );
      },
    });

    const result = await api.corefs.operation({
      operation: "stat",
      path: "Diary/today.md",
      searchMode: "exact",
    });

    expect(requestedUrl).toBe("https://api.test/api/corefs/operation");
    expect(requestBody).toEqual({
      operation: "stat",
      path: "Diary/today.md",
      searchMode: "exact",
    });
    expect(requestHeaders?.get("x-anima-unlock")).toBe("unlock-token");
    expect(requestHeaders?.get("x-anima-nonce")).toBe("sidecar-nonce");
    expect(requestHeaders?.get("x-anima-corefs-principal")).toBeNull();
    expect(requestHeaders?.get("x-anima-corefs-client-id")).toBeNull();
    expect(requestHeaders?.get("x-anima-corefs-install-digest")).toBeNull();
    expect(result.principal.kind).toBe("user");
  });

  test("preserves structured CoreFS error codes for caller recovery", async () => {
    const detail = {
      code: "corefs_cursor_generation_mismatch",
      cursorGeneration: 8,
      selectedGeneration: 9,
    };
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      fetchImpl: async () =>
        new Response(JSON.stringify({ detail }), {
          status: 409,
          statusText: "Conflict",
        }),
    });

    let caught: unknown;
    try {
      await api.corefs.operation({
        operation: "list",
        path: "Notes",
        cursorAfter: "Notes/A.md",
        cursorGeneration: 8,
      });
    } catch (error) {
      caught = error;
    }

    expect(caught).toBeInstanceOf(Error);
    expect((caught as Error & { code?: string }).code).toBe(
      "corefs_cursor_generation_mismatch",
    );
    expect((caught as Error & { status?: number }).status).toBe(409);
    expect((caught as Error & { detail?: unknown }).detail).toEqual(detail);
  });

  test("serializes local ANIMA CORE transfer operations without archive bytes", async () => {
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
            operationId: "operation-a",
            payloadKind: "full",
            state: "prepared",
            phase: "prepared",
            selectedBytes: 10,
            bytesPublished: 0,
            progressPercent: 0,
            publicationMode: "single_file",
            declaredVolumeCount: 1,
            resultPath: null,
            archiveId: null,
            errorCode: null,
          }),
        );
      },
    });

    await api.corefs.transfer.prepare({
      destination: "/Volumes/Backup",
      passphrase: "correct horse battery staple",
      payloadKind: "full",
    });
    await api.corefs.transfer.operation("operation-a");
    await api.corefs.transfer.cancel("operation-a");

    expect(requests).toEqual([
      {
        url: "https://api.test/api/corefs/transfer/prepare",
        method: "POST",
        body: {
          destination: "/Volumes/Backup",
          passphrase: "correct horse battery staple",
          payloadKind: "full",
        },
      },
      {
        url: "https://api.test/api/corefs/transfer/operations/operation-a",
        method: "GET",
        body: undefined,
      },
      {
        url: "https://api.test/api/corefs/transfer/operations/operation-a/cancel",
        method: "POST",
        body: undefined,
      },
    ]);
    expect(JSON.stringify(requests)).not.toContain("vault");
  });

  test("serializes non-activating restore staging operations", async () => {
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
            operationId: "import-a",
            state: "prepared",
            phase: "prepared",
            archiveBytes: 10,
            bytesProcessed: 0,
            progressPercent: 0,
            payloadKind: null,
            recoveryState: null,
            archiveId: null,
            activationId: null,
            restartRequired: false,
            credentialsReplaced: false,
            recoveryExportOperationId: null,
            errorCode: null,
          }),
        );
      },
    });

    await api.corefs.transfer.probeImport("/Volumes/Backup/core.anima", "/Users/alice");
    await api.corefs.transfer.prepareImport({
      archivePath: "/Volumes/Backup/core.anima",
      stagingParent: "/Users/alice",
      passphrase: "correct horse battery staple",
    });
    await api.corefs.transfer.importOperation("import-a");
    await api.corefs.transfer.cancelImport("import-a");
    await api.corefs.transfer.activateImportOnRestart("import-a");
    await api.corefs.transfer.attachCoreFsRecovery("import-a");
    await api.corefs.transfer.replaceCoreFsRecoveryCredentials("import-a", {
      sourceCredentialKind: "password",
      sourceCredential: "old portable password",
      newPassword: "new portable password",
      confirmed: true,
    });
    await api.corefs.transfer.exportCoreFsRecovery("import-a", {
      destination: "/Volumes/Recovery",
      finalName: "recovered-fs.anima",
      passphrase: "new archive passphrase",
      credentialKind: "recovery",
      credential: "one request phrase",
    });
    await api.corefs.transfer.browseCoreFsRecovery("import-a", {
      operation: "list",
      credentialKind: "recovery",
      credential: "one request phrase",
      path: "",
    });

    expect(requests.map((request) => request.url)).toEqual([
      "https://api.test/api/corefs/transfer/import/probe",
      "https://api.test/api/corefs/transfer/import/prepare",
      "https://api.test/api/corefs/transfer/import/operations/import-a",
      "https://api.test/api/corefs/transfer/import/operations/import-a/cancel",
      "https://api.test/api/corefs/transfer/import/operations/import-a/activate-on-restart",
      "https://api.test/api/corefs/transfer/import/operations/import-a/attach-corefs",
      "https://api.test/api/corefs/transfer/import/operations/import-a/replace-corefs-credentials",
      "https://api.test/api/corefs/transfer/import/operations/import-a/export-corefs",
      "https://api.test/api/corefs/transfer/import/operations/import-a/browse-corefs",
    ]);
    expect(requests.at(-3)?.body).toEqual({
      sourceCredentialKind: "password",
      sourceCredential: "old portable password",
      newPassword: "new portable password",
      confirmed: true,
    });
    expect(requests.at(-2)?.body).toEqual({
      destination: "/Volumes/Recovery",
      finalName: "recovered-fs.anima",
      passphrase: "new archive passphrase",
      credentialKind: "recovery",
      credential: "one request phrase",
    });
    expect(requests.at(-1)?.body).toEqual({
      operation: "list",
      credentialKind: "recovery",
      credential: "one request phrase",
      path: "",
    });
  });

  test("requires explicit confirmation for restart-only retained Core rollback", async () => {
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
            generation: 3,
            activeCoreId: "active-core",
            retainedCoreId: "retained-core",
            activationId: "activation-a",
            rollbackScheduled: init?.method === "POST",
          }),
        );
      },
    });

    await api.corefs.transfer.activeCore();
    await api.corefs.transfer.rollbackOnRestart();

    expect(requests).toEqual([
      {
        url: "https://api.test/api/corefs/transfer/active-core",
        method: "GET",
        body: undefined,
      },
      {
        url: "https://api.test/api/corefs/transfer/active-core/rollback-on-restart",
        method: "POST",
        body: { confirmed: true },
      },
    ]);
  });

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
                  "Current companion state: Tracking the agent state line handoff. Recent emotion: curious. Inner tone: settled, holding steady.",
                source: "agent_state",
              },
            ],
            affectHint: "settled, holding steady",
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
    expect(state.affectHint).toBe("settled, holding steady");
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

  test("calls parsing-pack and reparse endpoints with auth headers", async () => {
    const requests: Array<{
      url: string;
      method: string;
      unlockHeader: string | null;
      nonceHeader: string | null;
      body?: unknown;
    }> = [];
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      getUnlockToken: () => "unlock-token",
      getNonce: () => "sidecar-nonce",
      fetchImpl: async (input, init) => {
        const headers = new Headers(init?.headers);
        requests.push({
          url: String(input),
          method: init?.method || "GET",
          unlockHeader: headers.get("x-anima-unlock"),
          nonceHeader: headers.get("x-anima-nonce"),
          body: init?.body ? JSON.parse(String(init.body)) : undefined,
        });
        if (String(input).endsWith("/reparse")) {
          return new Response(
            JSON.stringify({ status: "upgraded", chunk_count: 12 }),
          );
        }
        return new Response(
          JSON.stringify({ state: "ready", progress: null, error: null }),
        );
      },
    });

    const packStatus = await api.documents.parsingPack();
    const downloadStatus = await api.documents.downloadParsingPack();
    const reparseResult = await api.documents.reparse(42);

    expect(requests).toEqual([
      {
        url: "https://api.test/api/documents/parsing-pack",
        method: "GET",
        unlockHeader: "unlock-token",
        nonceHeader: "sidecar-nonce",
        body: undefined,
      },
      {
        url: "https://api.test/api/documents/parsing-pack/download",
        method: "POST",
        unlockHeader: "unlock-token",
        nonceHeader: "sidecar-nonce",
        body: undefined,
      },
      {
        url: "https://api.test/api/documents/42/reparse",
        method: "POST",
        unlockHeader: "unlock-token",
        nonceHeader: "sidecar-nonce",
        body: undefined,
      },
    ]);
    expect(packStatus).toEqual({ state: "ready", progress: null, error: null });
    expect(downloadStatus).toEqual({ state: "ready", progress: null, error: null });
    expect(reparseResult).toEqual({ status: "upgraded", chunk_count: 12 });
  });

  test("requests system capabilities with auth headers", async () => {
    let requestedUrl = "";
    let requestMethod = "";
    let requestHeaders: Headers | null = null;
    const capabilities = {
      parsingPack: { state: "ready", progress: null, error: null },
      embeddings: {
        provider: "fastembed",
        model: "BAAI/bge-small-en-v1.5",
        dim: 384,
        backend: "ready",
      },
      reranker: {
        enabled: true,
        model: "Xenova/ms-marco-MiniLM-L-6-v2",
        backend: "cold",
      },
      llm: { configured: true },
      contextualChunks: true,
      fullDocumentContext: true,
    };
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      getUnlockToken: () => "unlock-token",
      getNonce: () => "sidecar-nonce",
      fetchImpl: async (input, init) => {
        requestedUrl = String(input);
        requestMethod = init?.method || "GET";
        requestHeaders = new Headers(init?.headers);
        return new Response(JSON.stringify(capabilities));
      },
    });

    const result = await api.system.capabilities();

    expect(requestedUrl).toBe("https://api.test/api/capabilities");
    expect(requestMethod).toBe("GET");
    expect(requestHeaders?.get("x-anima-unlock")).toBe("unlock-token");
    expect(requestHeaders?.get("x-anima-nonce")).toBe("sidecar-nonce");
    expect(result).toEqual(capabilities);
    expect(result.embeddings.backend).toBe("ready");
    expect(result.llm.configured).toBe(true);
  });

  test("gets agent config including resolved embedding fields", async () => {
    let requestedUrl = "";
    let requestHeaders: Headers | null = null;
    const config = {
      provider: "ollama",
      model: "vaultbox/qwen3.5-uncensored:35b",
      extractionModel: null,
      ollamaUrl: null,
      hasApiKey: false,
      systemPrompt: null,
      embeddingProvider: "fastembed",
      embeddingModel: "BAAI/bge-small-en-v1.5",
      embeddingIsExplicit: false,
      hasEmbeddingApiKey: false,
    };
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      getUnlockToken: () => "unlock-token",
      getNonce: () => "sidecar-nonce",
      fetchImpl: async (input, init) => {
        requestedUrl = String(input);
        requestHeaders = new Headers(init?.headers);
        return new Response(JSON.stringify(config));
      },
    });

    const result = await api.config.get(1);

    expect(requestedUrl).toBe("https://api.test/api/config/1");
    expect(requestHeaders?.get("x-anima-unlock")).toBe("unlock-token");
    expect(requestHeaders?.get("x-anima-nonce")).toBe("sidecar-nonce");
    expect(result).toEqual(config);
    expect(result.embeddingProvider).toBe("fastembed");
    expect(result.embeddingIsExplicit).toBe(false);
  });

  test("updates agent config with embedding provider fields", async () => {
    let requestedUrl = "";
    let requestMethod = "";
    let requestBody: unknown;
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      getUnlockToken: () => "unlock-token",
      getNonce: () => "sidecar-nonce",
      fetchImpl: async (input, init) => {
        requestedUrl = String(input);
        requestMethod = init?.method || "GET";
        requestBody = init?.body ? JSON.parse(String(init.body)) : undefined;
        return new Response(JSON.stringify({ status: "updated" }));
      },
    });

    const result = await api.config.update(1, {
      provider: "ollama",
      model: "vaultbox/qwen3.5-uncensored:35b",
      embeddingProvider: "openai",
      embeddingModel: "text-embedding-3-small",
      embeddingApiKey: "sk-embed-test",
    });

    expect(requestedUrl).toBe("https://api.test/api/config/1");
    expect(requestMethod).toBe("PUT");
    expect(requestBody).toEqual({
      provider: "ollama",
      model: "vaultbox/qwen3.5-uncensored:35b",
      embeddingProvider: "openai",
      embeddingModel: "text-embedding-3-small",
      embeddingApiKey: "sk-embed-test",
    });
    expect(result).toEqual({ status: "updated" });
  });

  test("resets embedding provider to bundled default with empty string", async () => {
    let requestBody: unknown;
    const api = createApiClient({
      baseUrl: "https://api.test/api",
      getUnlockToken: () => "unlock-token",
      getNonce: () => "sidecar-nonce",
      fetchImpl: async (_input, init) => {
        requestBody = init?.body ? JSON.parse(String(init.body)) : undefined;
        return new Response(JSON.stringify({ status: "updated" }));
      },
    });

    await api.config.update(1, {
      provider: "ollama",
      model: "vaultbox/qwen3.5-uncensored:35b",
      embeddingProvider: "",
    });

    expect(requestBody).toEqual({
      provider: "ollama",
      model: "vaultbox/qwen3.5-uncensored:35b",
      embeddingProvider: "",
    });
  });

  test("calls knowledge library endpoints", async () => {
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
                  fileName: (body.get("file") as File).name,
                }
              : typeof body === "string"
                ? JSON.parse(body)
                : undefined,
        });
        if (String(input).endsWith("/knowledge/export?userId=7")) {
          return new Response(new Blob(["zip"]));
        }
        return new Response(
          JSON.stringify({
            sources: [],
            concepts: [],
            source: { id: 2, kind: "text", sourceUri: "text://a", contentHash: "h", status: "indexed" },
            artifacts: [],
            spans: [],
            id: 3,
            slug: "topic-a",
            title: "Topic A",
            conceptType: "topic",
            bodyMarkdown: "Body",
            frontmatter: {},
            metadata: null,
            status: "active",
            citations: [],
            links: [],
            compileRun: { id: 4, status: "completed", runType: "compiler:queued", sourceId: 2 },
            query: "topic",
            evidenceSpans: [],
            findings: [],
            conceptCount: 1,
            linkCount: 0,
          }),
        );
      },
    });

    await api.knowledge.listSources(7);
    await api.knowledge.readSource(7, 2);
    await api.knowledge.listConcepts(7);
    await api.knowledge.readConcept(7, 3);
    await api.knowledge.compileSource(7, 2);
    await api.knowledge.search(7, "topic");
    await api.knowledge.runLint(7, { sourceId: 2 });
    await api.knowledge.exportBundle(7);
    await api.knowledge.importBundle(7, new File(["zip"], "bundle.zip", { type: "application/zip" }));

    expect(requests).toEqual([
      {
        url: "https://api.test/api/knowledge/sources?userId=7",
        method: "GET",
        bodyType: "undefined",
      },
      {
        url: "https://api.test/api/knowledge/sources/2?userId=7",
        method: "GET",
        bodyType: "undefined",
      },
      {
        url: "https://api.test/api/knowledge/concepts?userId=7",
        method: "GET",
        bodyType: "undefined",
      },
      {
        url: "https://api.test/api/knowledge/concepts/3?userId=7",
        method: "GET",
        bodyType: "undefined",
      },
      {
        url: "https://api.test/api/knowledge/sources/2/compile?userId=7",
        method: "POST",
        bodyType: "undefined",
      },
      {
        url: "https://api.test/api/knowledge/search?userId=7&q=topic",
        method: "GET",
        bodyType: "undefined",
      },
      {
        url: "https://api.test/api/knowledge/lint",
        method: "POST",
        bodyType: "string",
        body: { userId: 7, sourceId: 2 },
      },
      {
        url: "https://api.test/api/knowledge/export?userId=7",
        method: "GET",
        bodyType: "undefined",
      },
      {
        url: "https://api.test/api/knowledge/import?userId=7",
        method: "POST",
        bodyType: "form",
        body: { fileName: "bundle.zip" },
      },
    ]);
  });

  describe("presence initiatives", () => {
    test("fetches pending initiatives and acknowledges by id", async () => {
      const calls: { url: string; method: string }[] = [];
      const api = createApiClient({
        baseUrl: "https://api.test/api",
        getUnlockToken: () => "unlock-token",
        fetchImpl: async (input, init) => {
          calls.push({ url: String(input), method: init?.method ?? "GET" });
          if (String(input).endsWith("/ack")) {
            return new Response(
              JSON.stringify({
                id: 7,
                drive: "closeness",
                text: "I kept thinking about the harbor photos.",
                createdAt: "2026-07-28T02:00:00+00:00",
                delivered: true,
                acknowledged: true,
              }),
            );
          }
          return new Response(
            JSON.stringify({
              userId: 42,
              initiatives: [
                {
                  id: 7,
                  drive: "closeness",
                  text: "I kept thinking about the harbor photos.",
                  createdAt: "2026-07-28T02:00:00+00:00",
                  delivered: true,
                  acknowledged: false,
                },
              ],
            }),
          );
        },
      });

      const list = await api.presence.initiatives(42);
      expect(list.userId).toBe(42);
      expect(list.initiatives).toHaveLength(1);
      expect(list.initiatives[0].id).toBe(7);
      expect(calls[0]).toEqual({
        url: "https://api.test/api/presence/42/initiatives",
        method: "GET",
      });

      const acked = await api.presence.ackInitiative(42, 7);
      expect(acked.acknowledged).toBe(true);
      expect(calls[1]).toEqual({
        url: "https://api.test/api/presence/42/initiatives/7/ack",
        method: "POST",
      });
    });

    test("sends the four inner-life presence-config fields on update", async () => {
      let requestBody: unknown = null;
      const api = createApiClient({
        baseUrl: "https://api.test/api",
        getUnlockToken: () => "unlock-token",
        fetchImpl: async (_input, init) => {
          requestBody = JSON.parse(String(init?.body));
          return new Response(
            JSON.stringify({
              userId: 42,
              enabled: true,
              mainChatEnabled: true,
              homeGreetingContextEnabled: true,
              taskNudgesEnabled: true,
              memoryNudgesEnabled: true,
              checkInNudgesEnabled: true,
              customInstruction: null,
              initiativeEnabled: true,
              quietHoursStart: 22,
              quietHoursEnd: 7,
              dreamSharing: "ambient",
            }),
          );
        },
      });

      const config = await api.presence.update(42, {
        initiativeEnabled: true,
        quietHoursStart: 22,
        quietHoursEnd: 7,
        dreamSharing: "ambient",
      });

      expect(requestBody).toEqual({
        initiativeEnabled: true,
        quietHoursStart: 22,
        quietHoursEnd: 7,
        dreamSharing: "ambient",
      });
      expect(config.initiativeEnabled).toBe(true);
      expect(config.dreamSharing).toBe("ambient");
    });
  });
});
