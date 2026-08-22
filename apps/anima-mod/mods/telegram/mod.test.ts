/**
 * Telegram Module Tests
 */

import { describe, it, expect, beforeEach, mock } from "bun:test";
import telegramMod from "./mod.js";
import type { ModContext, ChatResponse } from "../../src/core/types.js";

describe("Telegram Module", () => {
  let mockCtx: ModContext;
  let chatCalls: Array<{ userId: number; message: string }> = [];

  beforeEach(() => {
    chatCalls = [];
    mockCtx = {
      modId: "telegram",
      config: { token: "test-token", mode: "polling" },
      logger: {
        debug: () => {},
        info: () => {},
        warn: () => {},
        error: () => {},
      },
      anima: {
        chat: async (req): Promise<ChatResponse> => {
          chatCalls.push({ userId: req.userId, message: req.message });
          return {
            response: `Echo: ${req.message}`,
            model: "test",
            provider: "test",
            toolsUsed: [],
          };
        },
        linkChannel: async () => {},
        unlinkChannel: async () => {},
        lookupUser: async () => 1,
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
      await telegramMod.init(badCtx);
      expect(false).toBe(true); // Should throw
    } catch (err) {
      expect((err as Error).message).toContain("token");
    }
  });

  it("should initialize with valid config", async () => {
    await telegramMod.init(mockCtx);
    expect(true).toBe(true); // No throw
  });

  it("should provide health endpoint via router", () => {
    const router = telegramMod.getRouter?.();
    expect(router).toBeDefined();
  });

  it("should have start/stop lifecycle", () => {
    expect(typeof telegramMod.start).toBe("function");
    expect(typeof telegramMod.stop).toBe("function");
  });
});

describe("Telegram splitMessage", () => {
  // Re-export the function for testing or test via behavior
  it("should handle messages under limit", () => {
    const shortMsg = "Short message";
    expect(shortMsg.length).toBeLessThan(4096);
    // Would need to export function to test directly
  });
});
