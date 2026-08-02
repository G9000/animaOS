import { useCallback } from "react";
import { api } from "../../../lib/api";

// The single place a NEW inline image's bytes reach the encrypted
// attachment store, used by the diaryImage node (via DiaryEditor's
// onImageUpload prop -> createDiaryExtensions -> DiaryImage.configure) for
// all three of its insertion paths: clipboard paste, drag-and-drop, and the
// slash "/image" command's file picker. Deliberately independent of
// useDiaryEntries' own `uploadAttachment` (which also mirrors the result
// into the `entries` state's attachment list, for the Attach-button /
// cover-image / voice-note flows) — an inline image's source of truth is
// the attachmentId written into the document body itself, not the
// attachments array, so this hook does not need that extra bookkeeping.
// The server still records the exact same row either way; a page reload
// re-fetches the entry (attachments array included) from scratch.
//
// Returns null instead of throwing when entryId is null (point 6 of the
// task brief): attachment upload requires an existing entry id, and a
// brand-new entry is created eagerly (see DiaryWorkspace's startNewEntry)
// before the editor ever mounts, so in practice this only guards the
// unmounted-editor window, not a real user-facing gap.
export function useAttachmentUpload(
  entryId: number | null,
  onError?: (message: string) => void,
): (file: File) => Promise<number | null> {
  return useCallback(
    async (file: File): Promise<number | null> => {
      if (entryId === null) return null;
      try {
        const uploaded = await api.diary.uploadAttachment(entryId, file);
        return uploaded.id;
      } catch (err) {
        onError?.(err instanceof Error ? err.message : `Failed to attach "${file.name}".`);
        return null;
      }
    },
    [entryId, onError],
  );
}
