export interface LegacyDiaryDraftCandidate {
  storageKey: string;
  targetEntryId: number | null;
  html: string;
  title: string;
  mood: string;
  entryDate: string;
  updatedAt: string;
}

interface StorageReader {
  readonly length: number;
  key(index: number): string | null;
  getItem(key: string): string | null;
}

export function collectLegacyDiaryDrafts(
  storage: StorageReader,
  userId: number,
  now: () => Date = () => new Date(),
): LegacyDiaryDraftCandidate[] {
  const prefix = `anima:diary:draft:${userId}:`;
  const drafts: LegacyDiaryDraftCandidate[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const storageKey = storage.key(index);
    if (!storageKey?.startsWith(prefix)) continue;
    try {
      const value = JSON.parse(storage.getItem(storageKey) ?? "") as Record<string, unknown>;
      const mode = storageKey.slice(prefix.length);
      const edit = /^edit-(\d+)$/.exec(mode);
      drafts.push({
        storageKey,
        targetEntryId: edit ? Number(edit[1]) : null,
        html: typeof value.html === "string" ? value.html : "",
        title: typeof value.title === "string" ? value.title : "",
        mood: typeof value.mood === "string" ? value.mood : "",
        entryDate:
          typeof value.entryDate === "string"
            ? value.entryDate
            : now().toISOString().slice(0, 10),
        updatedAt:
          typeof value.updatedAt === "string" ? value.updatedAt : now().toISOString(),
      });
    } catch {
      // Malformed browser state remains untouched for manual recovery.
    }
  }
  return drafts;
}
