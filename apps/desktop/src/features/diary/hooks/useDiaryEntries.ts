import { useCallback, useEffect, useState } from "react";
import type {
  DiaryAttachmentData,
  DiaryEntryData,
  DiaryEntryUpdateData,
  DiaryFolderData,
} from "@anima/api-client";
import { api } from "../../../lib/api";
import { BLANK_BODY_MARKER } from "../lib/pageLifecycle";
import { todayISODate } from "../lib/textFormat";

const MAX_ENTRY_LIMIT = 200;
const ENTRY_PAGE_SIZE = 100;

export interface UseDiaryEntriesResult {
  entries: DiaryEntryData[];
  folders: DiaryFolderData[];
  loading: boolean;
  error: string | null;
  // Deviation from the brief's literal return shape: DiaryWorkspace still
  // needs one place to surface user-facing messages that don't originate
  // from this hook's own CRUD calls (recording failures, inline-image
  // embed errors) so the error banner has a single source of truth. See
  // task-12-report.md.
  setError: (message: string | null) => void;
  entryLimit: number;
  canLoadMore: boolean;
  loadMore: () => void;
  reload: (showLoader?: boolean) => Promise<void>;
  reloadFolders: () => Promise<void>;
  // `folderId` is not in the brief's literal `createEntry()` signature, but
  // startNewEntry's pre-existing behavior assigns the new entry to whatever
  // folder is currently active in the sidebar — that's UI state the hook
  // does not own, so it has to come in as an argument.
  createEntry: (input?: { folderId?: number | null }) => Promise<DiaryEntryData | null>;
  // `errorMessage` lets each call site keep its own exact, pre-existing
  // user-facing string (point 7) instead of collapsing every failure into
  // one generic message.
  updateEntry: (
    id: number,
    data: DiaryEntryUpdateData,
    errorMessage?: string,
  ) => Promise<DiaryEntryData | null>;
  deleteEntry: (id: number) => Promise<boolean>;
  // Silent, no-refresh variant used only for the untitled-page cleanup —
  // see the doc comment on its implementation below.
  discardEntrySilently: (id: number) => Promise<boolean>;
  moveEntryToFolder: (entryId: number, folderId: number | null) => Promise<void>;
  uploadAttachment: (
    entryId: number,
    file: File,
    errorMessage?: string,
  ) => Promise<DiaryAttachmentData | null>;
  // Deliberately raw (throws, does not catch) — used only by the autosave
  // scheduler's `save`, which relies on rejection to drive its own
  // saving/error/retry status machine (see lib/autosaveScheduler.ts). Do
  // not route autosave through `updateEntry`, which swallows errors.
  saveEntryFields: (entryId: number, payload: { title: string; body: string }) => Promise<void>;
  downloadAttachment: (entryId: number, attachmentId: number) => Promise<Blob>;
  createFolder: (name: string) => Promise<DiaryFolderData | null>;
  renameFolder: (folderId: number, name: string) => Promise<DiaryFolderData | null>;
  deleteFolder: (folderId: number) => Promise<boolean>;
}

/**
 * The diary feature's data layer: every `api.diary.*` call, moved here from
 * DiaryWorkspace.tsx (Task 12, Step 1) verbatim — same error-message
 * strings, same success/failure state updates, same request shapes. The
 * component that consumes this keeps only selection and UI state.
 */
export function useDiaryEntries(userId: number | null): UseDiaryEntriesResult {
  const [entries, setEntries] = useState<DiaryEntryData[]>([]);
  const [folders, setFolders] = useState<DiaryFolderData[]>([]);
  const [entryLimit, setEntryLimit] = useState(ENTRY_PAGE_SIZE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(
    async (showLoader = true) => {
      if (userId == null) return;
      if (showLoader) setLoading(true);
      try {
        const diaryEntries = await api.diary.list(userId, entryLimit);
        setEntries(diaryEntries);
        setError(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load diary.");
      } finally {
        if (showLoader) setLoading(false);
      }
    },
    [userId, entryLimit],
  );

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId, entryLimit]);

  const reloadFolders = useCallback(async () => {
    if (userId == null) return;
    try {
      const list = await api.diary.folders.list(userId);
      setFolders(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load folders.");
    }
  }, [userId]);

  useEffect(() => {
    void reloadFolders();
  }, [reloadFolders]);

  const createEntry = useCallback(
    async (input?: { folderId?: number | null }): Promise<DiaryEntryData | null> => {
      if (userId == null) return null;
      try {
        const created = await api.diary.create(userId, {
          entryDate: todayISODate(),
          title: null,
          body: BLANK_BODY_MARKER,
          mood: null,
          folderId: input?.folderId ?? null,
        });
        setEntries((current) => [created, ...current]);
        return created;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to create diary entry.");
        return null;
      }
    },
    [userId],
  );

  const updateEntry = useCallback(
    async (
      id: number,
      data: DiaryEntryUpdateData,
      errorMessage?: string,
    ): Promise<DiaryEntryData | null> => {
      try {
        const updated = await api.diary.update(id, data);
        setEntries((current) => current.map((e) => (e.id === updated.id ? updated : e)));
        return updated;
      } catch (err) {
        setError(err instanceof Error ? err.message : (errorMessage ?? "Failed to update diary entry."));
        return null;
      }
    },
    [],
  );

  const saveEntryFields = useCallback(
    async (entryId: number, payload: { title: string; body: string }): Promise<void> => {
      const trimmedTitle = payload.title.trim();
      const updated = await api.diary.update(entryId, {
        body: payload.body,
        title: trimmedTitle || undefined,
        clearTitle: !trimmedTitle,
      });
      setEntries((current) => current.map((e) => (e.id === updated.id ? updated : e)));
    },
    [],
  );

  const deleteEntry = useCallback(
    async (id: number): Promise<boolean> => {
      try {
        await api.diary.delete(id);
        setEntries((current) => current.filter((entry) => entry.id !== id));
        await Promise.all([reload(false), reloadFolders()]);
        return true;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to delete diary entry.");
        return false;
      }
    },
    [reload, reloadFolders],
  );

  // Best-effort cleanup for a page the user created but never touched
  // (lib/pageLifecycle.ts's isDiscardablePage). Deliberately NOT the same
  // as `deleteEntry`: this must stay silent on failure (a stray empty page
  // left behind is no worse than before this cleanup existed — surfacing
  // "Failed to delete diary entry." for something the user never asked to
  // delete would be a regression) and must NOT refresh entries/folders the
  // way an explicit, user-confirmed delete does.
  const discardEntrySilently = useCallback(async (id: number): Promise<boolean> => {
    try {
      await api.diary.delete(id);
      setEntries((current) => current.filter((entry) => entry.id !== id));
      return true;
    } catch {
      return false;
    }
  }, []);

  const moveEntryToFolder = useCallback(
    async (entryId: number, folderId: number | null): Promise<void> => {
      const updated = await updateEntry(
        entryId,
        { folderId: folderId ?? undefined, clearFolder: folderId == null },
        "Failed to move entry.",
      );
      if (updated) await reloadFolders();
    },
    [updateEntry, reloadFolders],
  );

  const uploadAttachment = useCallback(
    async (
      entryId: number,
      file: File,
      errorMessage?: string,
    ): Promise<DiaryAttachmentData | null> => {
      try {
        const uploaded = await api.diary.uploadAttachment(entryId, file);
        setEntries((current) =>
          current.map((entry) =>
            entry.id === entryId
              ? { ...entry, attachments: [...entry.attachments, uploaded] }
              : entry,
          ),
        );
        return uploaded;
      } catch (err) {
        setError(err instanceof Error ? err.message : (errorMessage ?? `Failed to attach "${file.name}".`));
        return null;
      }
    },
    [],
  );

  const downloadAttachment = useCallback(
    (entryId: number, attachmentId: number): Promise<Blob> =>
      api.diary.downloadAttachment(entryId, attachmentId),
    [],
  );

  const createFolder = useCallback(
    async (name: string): Promise<DiaryFolderData | null> => {
      const trimmed = name.trim();
      if (!trimmed || userId == null) return null;
      try {
        const folder = await api.diary.folders.create(userId, trimmed);
        setFolders((current) => [...current, folder]);
        return folder;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to create folder.");
        return null;
      }
    },
    [userId],
  );

  const renameFolder = useCallback(
    async (folderId: number, name: string): Promise<DiaryFolderData | null> => {
      const trimmed = name.trim();
      if (!trimmed) return null;
      try {
        const updated = await api.diary.folders.rename(folderId, trimmed);
        setFolders((current) => current.map((folder) => (folder.id === folderId ? updated : folder)));
        return updated;
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to rename folder.");
        return null;
      }
    },
    [],
  );

  const deleteFolder = useCallback(async (folderId: number): Promise<boolean> => {
    try {
      await api.diary.folders.delete(folderId);
      setFolders((current) => current.filter((folder) => folder.id !== folderId));
      setEntries((current) =>
        current.map((entry) => (entry.folderId === folderId ? { ...entry, folderId: null } : entry)),
      );
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete folder.");
      return false;
    }
  }, []);

  const canLoadMore = entries.length >= entryLimit && entryLimit < MAX_ENTRY_LIMIT;
  const loadMore = useCallback(() => {
    setEntryLimit((limit) => Math.min(MAX_ENTRY_LIMIT, limit + ENTRY_PAGE_SIZE));
  }, []);

  return {
    entries,
    folders,
    loading,
    error,
    setError,
    entryLimit,
    canLoadMore,
    loadMore,
    reload,
    reloadFolders,
    createEntry,
    updateEntry,
    deleteEntry,
    discardEntrySilently,
    moveEntryToFolder,
    uploadAttachment,
    saveEntryFields,
    downloadAttachment,
    createFolder,
    renameFolder,
    deleteFolder,
  };
}
