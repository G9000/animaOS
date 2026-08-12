import type {
  DiaryDraftCompletionToken,
  DiaryDraftImportData,
  DiaryDraftImportResult,
} from "@anima/api-client";

export interface DraftStorage {
  readonly length: number;
  key(index: number): string | null;
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
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
  storageKey: string;
  userId: number;
  sanitizeHtml: (html: string) => string;
  importDraft: (request: DiaryDraftImportData) => Promise<DiaryDraftImportResult>;
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

function matchesToken(draft: NormalizedStoredDraft, token: DiaryDraftCompletionToken): boolean {
  return (
    draft.clientRevision === token.clientRevision &&
    draft.contentSha256 === token.contentSha256
  );
}

async function retainLatestDraft(
  storage: DraftStorage,
  storageKey: string,
  sanitizeHtml: (html: string) => string,
  minimumRevision: number,
  completionToken: DiaryDraftCompletionToken,
): Promise<LegacyDraftMigrationOutcome> {
  let raw: string | null;
  try {
    raw = storage.getItem(storageKey);
  } catch {
    return { status: "retry", retry: true, completionToken };
  }
  if (raw === null) return { status: "removed", retry: false, completionToken };
  const retained = await normalizeStoredDraft(raw, sanitizeHtml, minimumRevision);
  try {
    // Hashing yields to the browser. Never overwrite a still-newer edit that
    // arrived while the exact value above was being normalized.
    if (storage.getItem(storageKey) !== raw) {
      return { status: "retained", retry: true, completionToken };
    }
    storage.setItem(storageKey, JSON.stringify(retained));
  } catch {
    return { status: "retry", retry: true, completionToken };
  }
  return { status: "retained", retry: true, completionToken };
}

export async function migrateLegacyDiaryDraft(
  options: MigrateLegacyDiaryDraftOptions,
): Promise<LegacyDraftMigrationOutcome> {
  const { storage, storageKey, sanitizeHtml, importDraft } = options;
  let initialRaw: string | null;
  try {
    initialRaw = storage.getItem(storageKey);
  } catch {
    return { status: "retry", retry: true };
  }
  if (initialRaw === null) return { status: "missing", retry: false };

  const submitted = await normalizeStoredDraft(initialRaw, sanitizeHtml);
  try {
    storage.setItem(storageKey, JSON.stringify(submitted));
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
  let currentRaw: string | null;
  try {
    currentRaw = storage.getItem(storageKey);
  } catch {
    return { status: "retry", retry: true, completionToken: token };
  }
  if (currentRaw === null) return { status: "removed", retry: false, completionToken: token };

  const current = await normalizeStoredDraft(
    currentRaw,
    sanitizeHtml,
    token.clientRevision,
  );
  if (token.draftId === storageKey && matchesToken(current, token)) {
    try {
      // normalizeStoredDraft hashes asynchronously. Re-read immediately
      // before deletion so a value changed during hashing cannot be removed.
      if (storage.getItem(storageKey) !== currentRaw) {
        return retainLatestDraft(
          storage,
          storageKey,
          sanitizeHtml,
          token.clientRevision + 1,
          token,
        );
      }
      storage.removeItem(storageKey);
      return { status: "removed", retry: false, completionToken: token };
    } catch {
      return { status: "retry", retry: true, completionToken: token };
    }
  }

  // A legacy writer can replace the record while the request is in flight
  // without knowing about revisions. Stamp that newer value above the
  // completed revision before scheduling its retry; never overwrite its body.
  const retained = {
    ...current,
    clientRevision: Math.max(current.clientRevision, token.clientRevision + 1),
  };
  try {
    if (storage.getItem(storageKey) !== currentRaw) {
      return retainLatestDraft(
        storage,
        storageKey,
        sanitizeHtml,
        token.clientRevision + 1,
        token,
      );
    }
    storage.setItem(storageKey, JSON.stringify(retained));
  } catch {
    return { status: "retry", retry: true, completionToken: token };
  }
  return { status: "retained", retry: true, completionToken: token };
}

export async function migrateLegacyDiaryDrafts(options: {
  storage: DraftStorage;
  prefix: string;
  userId: number;
  sanitizeHtml: (html: string) => string;
  importDraft: (request: DiaryDraftImportData) => Promise<DiaryDraftImportResult>;
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
