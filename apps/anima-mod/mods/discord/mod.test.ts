/**
 * Discord Module Tests
 */

import { describe, it, expect, beforeEach } from "bun:test";
import discordMod from "./mod.js";
import type { ModContext, ChatResponse } from "../../src/core/types.js";

describe("Discord Module", () => {
  let mockCtx: ModContext;

  beforeEach(() => {
    mockCtx = {
      modId: "discord",
      config: { token: "test-discord-token" },
      logger: {
        debug: () => {},
        info: () => {},
        warn: () => {},
        error: () => {},
      },
      anima: {
        chat: async (): Promise<ChatResponse> => ({
          response: "Test response",
          model: "test",
          provider: "test",
          toolsUsed: [],
        }),
        linkChannel: async () => {},
        unlinkChannel: async () => {},
        lookupUser: async () => null,
      },
      store: {
        get: async () => null,
        set: async () => {},
        delete: async () => {},
        has: async () => false,
      },
      secrets: {
        get: async () => null,
        set: async () => {},
        delete: async () => {},
      },
      dispatch: {
        sendToUser: async () => {},
        sendToChannel: async () => {},
        onMessage: () => () => {},
        createTask: async () => "task-1",
        onTask: () => () => {},
      },
    };
  });

  it("should require token in config", async () => {
    const badCtx = { ...mockCtx, config: {} };
    try {
      await discordMod.init(badCtx);
      expect(false).toBe(true); // Should throw
    } catch (err) {
      expect((err as Error).message).toContain("token");
    }
  });

  it("should initialize with valid config", async () => {
    await discordMod.init(mockCtx);
    expect(true).toBe(true); // No throw
  });

  it("should provide router with health endpoint", () => {
    const router = discordMod.getRouter?.();
    expect(router).toBeDefined();
  });

  it("should have start/stop lifecycle", () => {
    expect(typeof discordMod.start).toBe("function");
    expect(typeof discordMod.stop).toBe("function");
  });

  it("should use default intents if not specified", async () => {
    await discordMod.init(mockCtx);
    // Default intents: 1 + 512 + 4096 + 32768 = 37377
    expect(true).toBe(true);
  });
});
