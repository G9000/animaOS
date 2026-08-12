import { describe, expect, test } from "bun:test";

import {
  migrateLegacyDiaryDraft,
  type DraftStorage,
} from "../src/features/diary/lib/draftMigration";

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
  test("retains and advances a newer local edit when an older completion arrives", async () => {
    const storage = new MemoryStorage();
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
    expect(retained.clientRevision).toBe(2);
    expect(retained.contentSha256).not.toBe(outcome.completionToken?.contentSha256);
  });

  test("increments a persisted revision when its body hash changed", async () => {
    const storage = new MemoryStorage();
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
    expect(outcome.status).toBe("removed");
    expect(storage.getItem(key)).toBeNull();
  });

  test("rechecks storage after asynchronous hashing before deletion", async () => {
    const storage = new MemoryStorage();
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
    expect(retained.clientRevision).toBe(2);
  });
});
