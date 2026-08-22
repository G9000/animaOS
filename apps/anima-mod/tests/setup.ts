/**
 * Test Setup
 * 
 * Global test configuration and utilities
 * 
 * Usage: Import createMockContext() in your tests
 * Tests can be co-located with source: mods/my-mod/mod.test.ts
 */

import { beforeAll, afterAll } from "bun:test";

// Global test setup
beforeAll(() => {
  console.log("🧪 Starting anima-mod test suite");
});

// Global test teardown
afterAll(() => {
  console.log("✅ anima-mod test suite complete");
});

// Test utilities
export function createMockContext(overrides?: Partial<any>): any {
  return {
    modId: "test",
    config: {},
    logger: {
      debug: () => {},
      info: () => {},
      warn: () => {},
      error: () => {},
    },
    anima: {
      chat: async () => ({ response: "test", model: "test", provider: "test", toolsUsed: [] }),
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
    ...overrides,
  };
}
