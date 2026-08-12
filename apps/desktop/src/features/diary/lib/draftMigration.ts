import type {
  DiaryDraftCompletionToken,
  DiaryDraftImportData,
  DiaryDraftImportResult,
} from "@anima/api-client";
import {
  deriveDraftCleanupAudience,
  type DraftCleanupAuthority,
} from "../../../lib/draftCleanupAuthority";

const DRAFT_MIGRATION_RETRY_DELAYS_MS = [1_000, 2_000, 4_000, 8_000, 16_000] as const;

export function draftMigrationRetryDelay(attempt: number): number | null {
  return DRAFT_MIGRATION_RETRY_DELAYS_MS[attempt] ?? null;
}

export interface DraftStorage {
  readonly length: number;
  key(index: number): string | null;
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export interface DraftLockManager {
  request<T>(name: string, callback: () => Promise<T>): Promise<T>;
}

export function draftMigrationLockName(storageKey: string): string {
  return `anima:diary:draft-lock:v1:${storageKey}`;
}

interface StoredLegacyDraft {
  html?: unknown;
  body?: unknown;
  title?: unknown;
  mood?: unknown;
  entryDate?: unknown;
  updatedAt?: unknown;
  targetEntryId?: unknown;
  clientRevision?: unknown;
  contentSha256?: unknown;
}

interface NormalizedStoredDraft {
  html: string;
  title: string;
  mood: string;
  entryDate: string;
  updatedAt: string;
  targetEntryId: number | null;
  clientRevision: number;
  contentSha256: string;
}

export interface MigrateLegacyDiaryDraftOptions {
  storage: DraftStorage;
  lockManager: DraftLockManager;
  storageKey: string;
  userId: number;
  sanitizeHtml: (html: string) => string;
  importDraft: (request: DiaryDraftImportData) => Promise<DiaryDraftImportResult>;
  cleanupAuthority?: DraftCleanupAuthority;
}

export interface LegacyDraftMigrationOutcome {
  status: "missing" | "removed" | "retained" | "retry";
  retry: boolean;
  completionToken?: DiaryDraftCompletionToken;
}

export async function sha256Hex(value: string): Promise<string> {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function parseStoredDraft(raw: string): StoredLegacyDraft {
  try {
    const parsed = JSON.parse(raw) as unknown;
    if (typeof parsed === "object" && parsed !== null) return parsed as StoredLegacyDraft;
  } catch {
    // The oldest draft shape was allowed to be the body string itself.
  }
  return { html: raw };
}

function stringValue(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function targetEntryId(value: unknown): number | null {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
}

async function normalizeStoredDraft(
  raw: string,
  sanitizeHtml: (html: string) => string,
  minimumRevision = 1,
): Promise<NormalizedStoredDraft> {
  const parsed = parseStoredDraft(raw);
  const html = sanitizeHtml(stringValue(parsed.html, stringValue(parsed.body)));
  const contentSha256 = await sha256Hex(html);
  const storedRevision =
    typeof parsed.clientRevision === "number" &&
    Number.isSafeInteger(parsed.clientRevision) &&
    parsed.clientRevision >= 1
      ? parsed.clientRevision
      : minimumRevision;
  const revisionMatches =
    parsed.contentSha256 === contentSha256 && storedRevision >= minimumRevision;
  const hadPersistedHash = typeof parsed.contentSha256 === "string";

  return {
    html,
    title: stringValue(parsed.title),
    mood: stringValue(parsed.mood),
    entryDate: stringValue(parsed.entryDate, new Date().toISOString().slice(0, 10)),
    updatedAt: stringValue(parsed.updatedAt, new Date().toISOString()),
    targetEntryId: targetEntryId(parsed.targetEntryId),
    clientRevision: revisionMatches
      ? storedRevision
      : Math.max(storedRevision + (hadPersistedHash ? 1 : 0), minimumRevision),
    contentSha256,
  };
}

interface DraftMigrationState {
  sourceSha256: string;
  clientRevision: number;
}

function draftMigrationStateKey(storageKey: string): string {
  return `anima:diary:draft-migration-state:v1:${storageKey}`;
}

function parseMigrationState(raw: string | null): DraftMigrationState | null {
  if (raw === null) return null;
  try {
    const value = JSON.parse(raw) as Partial<DraftMigrationState>;
    if (
      typeof value.sourceSha256 === "string" &&
      /^[0-9a-f]{64}$/.test(value.sourceSha256) &&
      typeof value.clientRevision === "number" &&
      Number.isSafeInteger(value.clientRevision) &&
      value.clientRevision >= 1
    ) {
      return value as DraftMigrationState;
    }
  } catch {
    // A malformed non-sensitive sidecar is replaced from the source value.
  }
  return null;
}

async function migrateLegacyDiaryDraftUnlocked(
  options: MigrateLegacyDiaryDraftOptions,
): Promise<LegacyDraftMigrationOutcome> {
  const { storage, storageKey, sanitizeHtml, importDraft, cleanupAuthority } = options;
  let initialRaw: string | null;
  try {
    initialRaw = storage.getItem(storageKey);
  } catch {
    return { status: "retry", retry: true };
  }
  if (initialRaw === null) return { status: "missing", retry: false };

  let submitted = await normalizeStoredDraft(initialRaw, sanitizeHtml);
  const sourceSha256 = await sha256Hex(initialRaw);
  try {
    // Hashing yields to the browser. Never mutate or import a superseded
    // source value.
    if (storage.getItem(storageKey) !== initialRaw) {
      return { status: "retained", retry: true };
    }
    const stateKey = draftMigrationStateKey(storageKey);
    const prior = parseMigrationState(storage.getItem(stateKey));
    const clientRevision =
      prior?.sourceSha256 === sourceSha256
        ? Math.max(submitted.clientRevision, prior.clientRevision)
        : Math.max(submitted.clientRevision, (prior?.clientRevision ?? 0) + 1);
    submitted = { ...submitted, clientRevision };
    // The sidecar contains only a source digest and monotonic counter. It can
    // safely advance without overwriting the legacy draft body, even when an
    // uncooperative older tab still writes that body key.
    storage.setItem(stateKey, JSON.stringify({ sourceSha256, clientRevision }));
    if (storage.getItem(storageKey) !== initialRaw) {
      return { status: "retained", retry: true };
    }
  } catch {
    return { status: "retry", retry: true };
  }

  let result: DiaryDraftImportResult;
  try {
    result = await importDraft({
      draftId: storageKey,
      clientRevision: submitted.clientRevision,
      contentSha256: submitted.contentSha256,
      targetEntryId: submitted.targetEntryId,
      html: submitted.html,
      title: submitted.title,
      mood: submitted.mood,
      entryDate: submitted.entryDate,
      updatedAt: submitted.updatedAt,
    });
  } catch {
    return { status: "retry", retry: true };
  }

  const token = result.completionToken;
  if (
    token.draftId !== storageKey ||
    token.clientRevision !== submitted.clientRevision ||
    token.contentSha256 !== submitted.contentSha256
  ) {
    return { status: "retry", retry: true };
  }
  let currentRaw: string | null;
  try {
    currentRaw = storage.getItem(storageKey);
  } catch {
    return { status: "retry", retry: true, completionToken: token };
  }
  if (currentRaw === null) return { status: "removed", retry: false, completionToken: token };
  if (currentRaw !== initialRaw) {
    return { status: "retained", retry: true, completionToken: token };
  }
  if (cleanupAuthority === undefined) {
    return { status: "retained", retry: false, completionToken: token };
  }

  let audience: string;
  let issued: Awaited<ReturnType<DraftCleanupAuthority["issue"]>>;
  try {
    const beforeIssue = await normalizeStoredDraft(currentRaw, sanitizeHtml, submitted.clientRevision);
    if (
      storage.getItem(storageKey) !== initialRaw ||
      beforeIssue.contentSha256 !== submitted.contentSha256 ||
      beforeIssue.clientRevision !== submitted.clientRevision
    ) {
      return { status: "retained", retry: true, completionToken: token };
    }
    audience = await deriveDraftCleanupAudience(storageKey, token);
    issued = await cleanupAuthority.issue(audience);
    if (
      !/^[0-9a-f]{64}$/.test(issued.capability) ||
      !Number.isSafeInteger(issued.expiresInMs) ||
      issued.expiresInMs < 1 ||
      issued.expiresInMs > 5_000
    ) {
      return { status: "retry", retry: true, completionToken: token };
    }
  } catch {
    return { status: "retry", retry: true, completionToken: token };
  }

  try {
    const beforeConsumeRaw = storage.getItem(storageKey);
    if (beforeConsumeRaw !== initialRaw) {
      await cleanupAuthority.consume(issued.capability, audience).catch(() => false);
      return { status: "retained", retry: true, completionToken: token };
    }
    const beforeConsume = await normalizeStoredDraft(
      beforeConsumeRaw,
      sanitizeHtml,
      submitted.clientRevision,
    );
    if (
      storage.getItem(storageKey) !== initialRaw ||
      beforeConsume.contentSha256 !== submitted.contentSha256 ||
      beforeConsume.clientRevision !== submitted.clientRevision
    ) {
      await cleanupAuthority.consume(issued.capability, audience).catch(() => false);
      return { status: "retained", retry: true, completionToken: token };
    }
    if (!(await cleanupAuthority.consume(issued.capability, audience))) {
      return { status: "retry", retry: true, completionToken: token };
    }
  } catch {
    return { status: "retry", retry: true, completionToken: token };
  }

  try {
    const finalRaw = storage.getItem(storageKey);
    if (finalRaw !== initialRaw) {
      return { status: "retained", retry: true, completionToken: token };
    }
    const finalDraft = await normalizeStoredDraft(finalRaw, sanitizeHtml, submitted.clientRevision);
    if (
      storage.getItem(storageKey) !== initialRaw ||
      finalDraft.contentSha256 !== submitted.contentSha256 ||
      finalDraft.clientRevision !== submitted.clientRevision
    ) {
      return { status: "retained", retry: true, completionToken: token };
    }
    storage.removeItem(storageKey);
    if (storage.getItem(storageKey) !== null) {
      return { status: "retained", retry: true, completionToken: token };
    }
    // Source-first crash ordering: a leftover sidecar is harmless, while
    // deleting it before proven source absence could strand a recreated draft.
    try {
      storage.removeItem(draftMigrationStateKey(storageKey));
    } catch {
      // Best effort only after the plaintext source is proven absent.
    }
    return { status: "removed", retry: false, completionToken: token };
  } catch {
    return { status: "retry", retry: true, completionToken: token };
  }
}

export async function migrateLegacyDiaryDraft(
  options: MigrateLegacyDiaryDraftOptions,
): Promise<LegacyDraftMigrationOutcome> {
  try {
    // Serialize current clients. Older clients do not honor this lock, so the
    // migration itself remains non-destructive to the legacy body key.
    return await options.lockManager.request(draftMigrationLockName(options.storageKey), () =>
      migrateLegacyDiaryDraftUnlocked(options),
    );
  } catch {
    return { status: "retry", retry: true };
  }
}

export async function migrateLegacyDiaryDrafts(options: {
  storage: DraftStorage;
  lockManager: DraftLockManager;
  prefix: string;
  userId: number;
  sanitizeHtml: (html: string) => string;
  importDraft: (request: DiaryDraftImportData) => Promise<DiaryDraftImportResult>;
  cleanupAuthority?: DraftCleanupAuthority;
}): Promise<boolean> {
  const keys: string[] = [];
  try {
    for (let index = 0; index < options.storage.length; index += 1) {
      const key = options.storage.key(index);
      if (key?.startsWith(options.prefix)) keys.push(key);
    }
  } catch {
    return true;
  }

  let retry = false;
  for (const storageKey of keys.sort()) {
    const outcome = await migrateLegacyDiaryDraft({ ...options, storageKey });
    retry ||= outcome.retry;
  }
  return retry;
}

export async function migrateLegacyDiaryDraftsFromStorageProvider(options: {
  storageProvider: () => DraftStorage;
  lockManagerProvider: () => DraftLockManager | undefined;
  prefix: string;
  userId: number;
  sanitizeHtml: (html: string) => string;
  importDraft: (request: DiaryDraftImportData) => Promise<DiaryDraftImportResult>;
  cleanupAuthority?: DraftCleanupAuthority;
}): Promise<boolean> {
  let storage: DraftStorage;
  let lockManager: DraftLockManager | undefined;
  try {
    storage = options.storageProvider();
    lockManager = options.lockManagerProvider();
  } catch {
    return true;
  }
  if (lockManager === undefined || typeof lockManager.request !== "function") return true;
  return migrateLegacyDiaryDrafts({ ...options, storage, lockManager });
}
