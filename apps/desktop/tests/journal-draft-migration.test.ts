import { describe, expect, test } from "bun:test";

import {
  draftMigrationLockName,
  draftMigrationRetryDelay,
  migrateLegacyDiaryDraft,
  migrateLegacyDiaryDraftsFromStorageProvider,
  type DraftStorage,
} from "../src/features/diary/lib/draftMigration";

class MemoryLockManager {
  private readonly held = new Set<string>();

  async request<T>(name: string, callback: () => Promise<T>): Promise<T> {
    if (this.held.has(name)) throw new Error("test lock is already held");
    this.held.add(name);
    try {
      return await callback();
    } finally {
      this.held.delete(name);
    }
  }

  isHeld(name: string): boolean {
    return this.held.has(name);
  }
}

class MemoryStorage implements DraftStorage {
  private readonly values = new Map<string, string>();

  get length(): number {
    return this.values.size;
  }

  key(index: number): string | null {
    return [...this.values.keys()][index] ?? null;
  }

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

describe("legacy Journal draft migration", () => {
  test("retries when browser storage cannot be acquired", async () => {
    const retry = await migrateLegacyDiaryDraftsFromStorageProvider({
      storageProvider: () => {
        throw new DOMException("Storage is disabled", "SecurityError");
      },
      lockManagerProvider: () => new MemoryLockManager(),
      prefix: "anima:diary:draft:7:",
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async () => {
        throw new Error("storage acquisition must fail first");
      },
    });

    expect(retry).toBe(true);
  });

  test("bounds repeated migration retries with exponential backoff", () => {
    expect([0, 1, 2, 3, 4, 5].map(draftMigrationRetryDelay)).toEqual([
      1_000,
      2_000,
      4_000,
      8_000,
      16_000,
      null,
    ]);
  });

  test("retains and advances a newer local edit when an older completion arrives", async () => {
    const storage = new MemoryStorage();
    const lockManager = new MemoryLockManager();
    const key = "anima:diary:draft:7:entry-4";
    storage.setItem(
      key,
      JSON.stringify({
        html: "<p>old revision</p>",
        title: "Private",
        mood: "calm",
        entryDate: "2026-08-12",
        updatedAt: "2026-08-12T12:00:00Z",
        targetEntryId: 4,
      }),
    );

    let releaseImport: (() => void) | undefined;
    const importStarted = Promise.withResolvers<void>();
    const importReleased = new Promise<void>((resolve) => {
      releaseImport = resolve;
    });
    const migration = migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => {
        importStarted.resolve();
        await importReleased;
        return {
          stableId: "01J00000000000000000000000",
          revision: 1,
          generation: 1,
          catalogHash: "a".repeat(64),
          verified: true,
          authoritative: false,
          completionToken: {
            draftId: request.draftId,
            clientRevision: request.clientRevision,
            contentSha256: request.contentSha256,
          },
        };
      },
    });

    await importStarted.promise;
    storage.setItem(
      key,
      JSON.stringify({
        html: "<p>newer local edit</p>",
        title: "Private",
        mood: "hopeful",
        entryDate: "2026-08-12",
        updatedAt: "2026-08-12T12:01:00Z",
        targetEntryId: 4,
      }),
    );
    releaseImport?.();

    const outcome = await migration;
    const retained = JSON.parse(storage.getItem(key) ?? "null") as Record<string, unknown>;
    expect(outcome.status).toBe("retained");
    expect(outcome.retry).toBe(true);
    expect(retained.html).toBe("<p>newer local edit</p>");
    expect(retained.clientRevision).toBeUndefined();
    expect(retained.contentSha256).not.toBe(outcome.completionToken?.contentSha256);

    let retriedRevision = 0;
    const retried = await migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => {
        retriedRevision = request.clientRevision;
        return {
          stableId: "01J00000000000000000000000",
          revision: 2,
          generation: 2,
          catalogHash: "b".repeat(64),
          verified: true,
          authoritative: false,
          completionToken: {
            draftId: request.draftId,
            clientRevision: request.clientRevision,
            contentSha256: request.contentSha256,
          },
        };
      },
    });
    expect(retriedRevision).toBe(2);
    expect(retried.status).toBe("retained");
    expect(retried.retry).toBe(false);
    expect(JSON.parse(storage.getItem(key) ?? "null").html).toBe("<p>newer local edit</p>");
  });

  test("retains a metadata-only edit even when its body hash is unchanged", async () => {
    const storage = new MemoryStorage();
    const lockManager = new MemoryLockManager();
    const key = "anima:diary:draft:7:entry-4-metadata";
    storage.setItem(
      key,
      JSON.stringify({
        html: "<p>same body</p>",
        title: "Before import",
        mood: "calm",
      }),
    );

    const importStarted = Promise.withResolvers<void>();
    const importReleased = Promise.withResolvers<void>();
    const migration = migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => {
        importStarted.resolve();
        await importReleased.promise;
        return {
          stableId: "01J00000000000000000000000",
          revision: 1,
          generation: 1,
          catalogHash: "a".repeat(64),
          verified: true,
          authoritative: false,
          completionToken: {
            draftId: request.draftId,
            clientRevision: request.clientRevision,
            contentSha256: request.contentSha256,
          },
        };
      },
    });

    await importStarted.promise;
    storage.setItem(
      key,
      JSON.stringify({
        html: "<p>same body</p>",
        title: "Edited during import",
        mood: "hopeful",
      }),
    );
    importReleased.resolve();

    const outcome = await migration;
    const retained = JSON.parse(storage.getItem(key) ?? "null") as Record<string, unknown>;
    expect(outcome.status).toBe("retained");
    expect(outcome.retry).toBe(true);
    expect(retained.title).toBe("Edited during import");
    expect(retained.mood).toBe("hopeful");
    expect(retained.clientRevision).toBeUndefined();
    expect(retained.contentSha256).toBeUndefined();
  });

  test("increments a persisted revision when its body hash changed", async () => {
    const storage = new MemoryStorage();
    const lockManager = new MemoryLockManager();
    const key = "anima:diary:draft:7:entry-5";
    storage.setItem(
      key,
      JSON.stringify({
        html: "<p>edited after revision four</p>",
        clientRevision: 4,
        contentSha256: "0".repeat(64),
      }),
    );
    let submittedRevision = 0;

    const outcome = await migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => {
        submittedRevision = request.clientRevision;
        return {
          stableId: "01J00000000000000000000000",
          revision: 2,
          generation: 2,
          catalogHash: "a".repeat(64),
          verified: true,
          authoritative: false,
          completionToken: {
            draftId: request.draftId,
            clientRevision: request.clientRevision,
            contentSha256: request.contentSha256,
          },
        };
      },
    });

    expect(submittedRevision).toBe(5);
    expect(outcome.status).toBe("retained");
    expect(outcome.retry).toBe(false);
    expect(storage.getItem(key)).not.toBeNull();
  });

  test("keeps the local draft when the server returns a mismatched completion token", async () => {
    const storage = new MemoryStorage();
    const lockManager = new MemoryLockManager();
    const key = "anima:diary:draft:7:entry-5-token";
    storage.setItem(key, JSON.stringify({ html: "<p>keep me</p>" }));

    const outcome = await migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => ({
        stableId: "01J00000000000000000000000",
        revision: 1,
        generation: 1,
        catalogHash: "a".repeat(64),
        verified: true,
        authoritative: false,
        completionToken: {
          draftId: request.draftId,
          clientRevision: request.clientRevision + 1,
          contentSha256: request.contentSha256,
        },
      }),
    });

    expect(outcome.status).toBe("retry");
    expect(outcome.retry).toBe(true);
    expect(JSON.parse(storage.getItem(key) ?? "null").html).toBe("<p>keep me</p>");
  });

  test("does not overwrite an edit made during initial asynchronous hashing", async () => {
    const storage = new MemoryStorage();
    const lockManager = new MemoryLockManager();
    const key = "anima:diary:draft:7:entry-5-initial-hash";
    storage.setItem(key, JSON.stringify({ html: "<p>initial</p>" }));
    const baseGet = storage.getItem.bind(storage);
    let firstRead = true;
    storage.getItem = (storageKey: string): string | null => {
      const value = baseGet(storageKey);
      if (firstRead) {
        firstRead = false;
        queueMicrotask(() => {
          storage.setItem(storageKey, JSON.stringify({ html: "<p>edited while hashing</p>" }));
        });
      }
      return value;
    };
    let importCalled = false;

    const outcome = await migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async () => {
        importCalled = true;
        throw new Error("the superseded value must not be imported");
      },
    });

    const retained = JSON.parse(storage.getItem(key) ?? "null") as Record<string, unknown>;
    expect(importCalled).toBe(false);
    expect(outcome.status).toBe("retained");
    expect(outcome.retry).toBe(true);
    expect(retained.html).toBe("<p>edited while hashing</p>");
    expect(retained.clientRevision).toBeUndefined();
  });

  test("rechecks storage after asynchronous hashing before deletion", async () => {
    const storage = new MemoryStorage();
    const lockManager = new MemoryLockManager();
    const key = "anima:diary:draft:7:entry-6";
    storage.setItem(key, JSON.stringify({ html: "<p>submitted</p>" }));
    const baseGet = storage.getItem.bind(storage);
    let reads = 0;
    storage.getItem = (storageKey: string): string | null => {
      reads += 1;
      const value = baseGet(storageKey);
      if (reads === 2) {
        queueMicrotask(() => {
          storage.setItem(storageKey, JSON.stringify({ html: "<p>late edit</p>" }));
        });
      }
      return value;
    };

    const outcome = await migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => ({
        stableId: "01J00000000000000000000000",
        revision: 1,
        generation: 1,
        catalogHash: "a".repeat(64),
        verified: true,
        authoritative: false,
        completionToken: {
          draftId: request.draftId,
          clientRevision: request.clientRevision,
          contentSha256: request.contentSha256,
        },
      }),
    });

    const retained = JSON.parse(storage.getItem(key) ?? "null") as Record<string, unknown>;
    expect(outcome.status).toBe("retained");
    expect(retained.html).toBe("<p>late edit</p>");
    expect(retained.clientRevision).toBeUndefined();
  });

  test("never deletes the legacy source while holding the cross-context lock", async () => {
    const lockManager = new MemoryLockManager();
    const key = "anima:diary:draft:7:entry-locked-delete";
    class LockCheckingStorage extends MemoryStorage {
      override removeItem(_storageKey: string): void {
        throw new Error("legacy draft deletion is unsafe while older writers can exist");
      }

      override setItem(storageKey: string, value: string): void {
        if (storageKey !== key && !lockManager.isHeld(draftMigrationLockName(key))) {
          throw new Error("migration sidecar write ran without the cross-context lock");
        }
        super.setItem(storageKey, value);
      }
    }
    const storage = new LockCheckingStorage();
    storage.setItem(key, JSON.stringify({ html: "<p>submitted</p>" }));

    const outcome = await migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => ({
        stableId: "01J00000000000000000000000",
        revision: 1,
        generation: 1,
        catalogHash: "a".repeat(64),
        verified: true,
        authoritative: false,
        completionToken: {
          draftId: request.draftId,
          clientRevision: request.clientRevision,
          contentSha256: request.contentSha256,
        },
      }),
    });

    expect(outcome.status).toBe("retained");
    expect(outcome.retry).toBe(false);
    expect(JSON.parse(storage.getItem(key) ?? "null").html).toBe("<p>submitted</p>");
  });
});
