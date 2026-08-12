import { createHash } from "node:crypto";
import { describe, expect, test } from "bun:test";
import type { DiaryDraftCompletionToken, DiaryDraftImportData } from "@anima/api-client";

import {
  deriveDraftCleanupAudience,
  draftCleanupAuthorityFromInvoke,
  type DraftCleanupAuthority,
} from "../src/lib/draftCleanupAuthority";
import {
  draftMigrationLockName,
  migrateLegacyDiaryDraft,
  type DraftStorage,
} from "../src/features/diary/lib/draftMigration";

const KEY_DOMAIN = "anima-draft-storage-key-v1\0";
const TOKEN_DOMAIN = "anima-draft-completion-token-v1\0";
const AUDIENCE_DOMAIN = "anima-draft-cleanup-audience-v1\0";

class MemoryLockManager {
  held: string | undefined;

  async request<T>(name: string, callback: () => Promise<T>): Promise<T> {
    if (this.held !== undefined) throw new Error("lock already held");
    this.held = name;
    try {
      return await callback();
    } finally {
      this.held = undefined;
    }
  }
}

class InstrumentedStorage implements DraftStorage {
  readonly values = new Map<string, string>();
  readonly operations: string[] = [];
  onGet?: (key: string, value: string | null) => void;
  beforeRemove?: (key: string) => void;
  onRemove?: (key: string) => void;

  get length(): number { return this.values.size; }
  key(index: number): string | null { return [...this.values.keys()][index] ?? null; }
  getItem(key: string): string | null {
    const value = this.values.get(key) ?? null;
    this.operations.push(`get:${key}:${value === null ? "null" : "value"}`);
    this.onGet?.(key, value);
    return value;
  }
  setItem(key: string, value: string): void {
    this.operations.push(`set:${key}`);
    this.values.set(key, value);
  }
  removeItem(key: string): void {
    this.operations.push(`remove:${key}`);
    this.beforeRemove?.(key);
    this.values.delete(key);
    this.onRemove?.(key);
  }
}

function u32(value: number): Buffer {
  const result = Buffer.alloc(4);
  result.writeUInt32BE(value);
  return result;
}

function u64(value: number): Buffer {
  const result = Buffer.alloc(8);
  result.writeBigUInt64BE(BigInt(value));
  return result;
}

function hash(...values: Array<string | Uint8Array>): Buffer {
  const digest = createHash("sha256");
  for (const value of values) digest.update(value);
  return digest.digest();
}

function expectedAudience(storageKey: string, token: DiaryDraftCompletionToken): string {
  const key = Buffer.from(storageKey.normalize("NFC"));
  const draftId = Buffer.from(token.draftId.normalize("NFC"));
  const keyDigest = hash(KEY_DOMAIN, u32(key.length), key);
  const tokenDigest = hash(
    TOKEN_DOMAIN,
    u32(draftId.length),
    draftId,
    u64(token.clientRevision),
    Buffer.from(token.contentSha256, "hex"),
  );
  return hash(AUDIENCE_DOMAIN, keyDigest, tokenDigest).toString("hex");
}

function successfulImport(request: DiaryDraftImportData) {
  return {
    stableId: "01J00000000000000000000000",
    revision: 1,
    generation: 1,
    catalogHash: "c".repeat(64),
    verified: true,
    authoritative: false,
    completionToken: {
      draftId: request.draftId,
      clientRevision: request.clientRevision,
      contentSha256: request.contentSha256,
    },
  };
}

function acceptingAuthority(hooks: {
  issue?: () => void;
  consume?: () => void;
} = {}): DraftCleanupAuthority {
  return {
    issue: async () => {
      hooks.issue?.();
      return { capability: "b".repeat(64), expiresInMs: 5_000 };
    },
    consume: async () => {
      hooks.consume?.();
      return true;
    },
  };
}

function fixture() {
  const storage = new InstrumentedStorage();
  const lockManager = new MemoryLockManager();
  const key = "anima:diary:draft:7:cleanup";
  const stateKey = `anima:diary:draft-migration-state:v1:${key}`;
  const raw = JSON.stringify({ html: "<p>private draft</p>", title: "Private" });
  storage.setItem(key, raw);
  return { storage, lockManager, key, stateKey, raw };
}

describe("draft cleanup audience", () => {
  test("uses NFC, length-delimited UTF-8, u64be revision, and raw hash bytes", async () => {
    const token = {
      draftId: "anima:diary:draft:7:cafe\u{301}",
      clientRevision: 4_294_967_297,
      contentSha256: "01".repeat(32),
    };
    const key = "anima:diary:draft:7:caf\u{e9}";
    expect(await deriveDraftCleanupAudience(key, token)).toBe(expectedAudience(key, token));
    expect(await deriveDraftCleanupAudience(key, token)).toBe(
      await deriveDraftCleanupAudience("anima:diary:draft:7:cafe\u{301}", token),
    );
  });

  test("rejects malformed hashes and revisions", async () => {
    await expect(deriveDraftCleanupAudience("key", {
      draftId: "draft",
      clientRevision: 1,
      contentSha256: "A".repeat(64),
    })).rejects.toThrow();
    await expect(deriveDraftCleanupAudience("key", {
      draftId: "draft",
      clientRevision: 0,
      contentSha256: "a".repeat(64),
    })).rejects.toThrow();
  });

  test("native invocation receives only the final audience and opaque capability", async () => {
    const calls: Array<[string, Record<string, unknown>]> = [];
    const authority = draftCleanupAuthorityFromInvoke(async (command, args) => {
      calls.push([command, args]);
      return (command.includes("issue")
        ? { capability: "c".repeat(64), expiresInMs: 5_000 }
        : true) as never;
    });
    await authority.issue("a".repeat(64));
    await authority.consume("c".repeat(64), "a".repeat(64));
    expect(calls).toEqual([
      ["draft_cleanup_issue_v1", { audienceDigest: "a".repeat(64) }],
      ["draft_cleanup_consume_v1", { capability: "c".repeat(64), audienceDigest: "a".repeat(64) }],
    ]);
    expect(JSON.stringify(calls)).not.toContain("contentSha256");
    expect(JSON.stringify(calls)).not.toContain("draftId");
  });
});

describe("authorized source-first draft cleanup", () => {
  test("removes the exact source before its sidecar while holding the per-key Web Lock", async () => {
    const { storage, lockManager, key, stateKey } = fixture();
    storage.onRemove = (removedKey) => {
      expect(lockManager.held).toBe(draftMigrationLockName(key));
      if (removedKey === stateKey) expect(storage.getItem(key)).toBeNull();
    };
    const outcome = await migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => successfulImport(request),
      cleanupAuthority: acceptingAuthority(),
    });
    expect(outcome.status).toBe("removed");
    expect(storage.getItem(key)).toBeNull();
    expect(storage.getItem(stateKey)).toBeNull();
    expect(storage.operations.indexOf(`remove:${key}`)).toBeLessThan(
      storage.operations.indexOf(`remove:${stateKey}`),
    );
  });

  test("retains a source changed while authority is issued and spends the capability", async () => {
    const { storage, lockManager, key } = fixture();
    let consumed = 0;
    const outcome = await migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => successfulImport(request),
      cleanupAuthority: {
        issue: async () => {
          storage.setItem(key, JSON.stringify({ html: "<p>new edit</p>" }));
          return { capability: "b".repeat(64), expiresInMs: 5_000 };
        },
        consume: async () => { consumed += 1; return true; },
      },
    });
    expect(outcome.status).toBe("retained");
    expect(outcome.retry).toBe(true);
    expect(consumed).toBe(1);
    expect(storage.getItem(key)).toContain("new edit");
  });

  test("retains a source changed during capability consumption", async () => {
    const { storage, lockManager, key } = fixture();
    const outcome = await migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => successfulImport(request),
      cleanupAuthority: acceptingAuthority({
        consume: () => storage.setItem(key, JSON.stringify({ html: "<p>after consume</p>" })),
      }),
    });
    expect(outcome.status).toBe("retained");
    expect(storage.getItem(key)).toContain("after consume");
  });

  test("a crash-like failure after source removal leaves the sidecar", async () => {
    const { storage, lockManager, key, stateKey } = fixture();
    storage.onRemove = (removedKey) => {
      if (removedKey === key) throw new Error("crash after removeItem(source)");
    };
    const outcome = await migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => successfulImport(request),
      cleanupAuthority: acceptingAuthority(),
    });
    expect(outcome.status).toBe("retry");
    expect(storage.getItem(key)).toBeNull();
    expect(storage.getItem(stateKey)).not.toBeNull();
  });

  test("sidecar cleanup is best effort only after verified source absence", async () => {
    const { storage, lockManager, key, stateKey } = fixture();
    storage.beforeRemove = (removedKey) => {
      if (removedKey === stateKey) throw new Error("sidecar storage failure");
    };
    const outcome = await migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => successfulImport(request),
      cleanupAuthority: acceptingAuthority(),
    });
    expect(outcome.status).toBe("removed");
    expect(storage.getItem(key)).toBeNull();
    expect(storage.getItem(stateKey)).not.toBeNull();
  });

  test("an orphan sidecar survives absence and advances a recreated legacy source", async () => {
    const { storage, lockManager, key, stateKey } = fixture();
    storage.values.delete(key);
    storage.setItem(stateKey, JSON.stringify({ sourceSha256: "a".repeat(64), clientRevision: 8 }));
    const missing = migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => successfulImport(request),
      cleanupAuthority: acceptingAuthority(),
    });
    storage.setItem(key, JSON.stringify({ html: "<p>legacy recreation</p>" }));
    expect((await missing).status).toBe("missing");
    expect(storage.getItem(stateKey)).not.toBeNull();

    let submittedRevision = 0;
    await migrateLegacyDiaryDraft({
      storage,
      lockManager,
      storageKey: key,
      userId: 7,
      sanitizeHtml: (html) => html,
      importDraft: async (request) => {
        submittedRevision = request.clientRevision;
        return successfulImport(request);
      },
    });
    expect(submittedRevision).toBe(9);
    expect(storage.getItem(key)).toContain("legacy recreation");
  });
});
