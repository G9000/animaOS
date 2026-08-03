import type { DiaryEntryUpdateData } from "@anima/api-client";

// PR #139 round 2, Finding 1 (P1): DetailsDrawer.tsx:224's unmount-flush
// commits the mood field onto the WRONG entry.
//
// Mechanism: DetailsDrawer is keyed by entry.id (fix round 1, Finding 3), so
// switching the selected entry unmounts the old keyed instance. Before that
// unmount cleanup runs, the parent (DiaryWorkspace) has already re-rendered
// with the NEW selection — `selectedEntryRef.current` is reassigned during
// the render body, which happens before the commit phase that tears the old
// DetailsDrawer down and fires its effect cleanup (see
// DiaryWorkspace.tsx's `selectedEntryRef.current = selectedEntry` and the
// `key={selectedEntry.id}` on DetailsDrawer). So by the time the outgoing
// instance's teardown calls `commitMoodValue(moodValueRef.current)` ->
// `onUpdate(...)`, a parent handler that re-derives "the current entry" from
// `selectedEntryRef.current` is reading the NEW entry, not the one the
// mood value actually belongs to.
//
// The fix: never re-derive the target from ambient "what's selected now"
// state. Bind the update to the entry it ORIGINATED from — the id the
// child (DetailsDrawer) was constructed with, which is stable for that
// keyed instance's entire lifetime — and always target that, regardless of
// what has been selected in the meantime.
//
// `currentlySelectedEntryId` is accepted here purely so the decision is
// visibly, testably independent of it — a future edit that "simplifies"
// this back to reading ambient selection state would have to touch this
// function and its tests, not silently regress by deleting a parameter no
// one appeared to be using.
export function resolveDrawerUpdateEntryId(
  originatingEntryId: number,
  _currentlySelectedEntryId: number | null,
): number {
  return originatingEntryId;
}

export interface DrawerUpdateHandlers {
  moveEntryToFolder: (entryId: number, folderId: number | null) => void;
  updateEntry: (entryId: number, data: DiaryEntryUpdateData, errorMessage?: string) => void;
}

/**
 * The single dispatch point for DetailsDrawer's generic `onUpdate(entryId,
 * data)` callback (see DetailsDrawer.tsx and DiaryWorkspace.tsx's
 * `handleDrawerUpdate`). Routes each field to the right underlying hook
 * call with its own pre-existing error message — folder changes need
 * `moveEntryToFolder` (which also refreshes folder entry counts), everything
 * else goes through the generic `updateEntry`.
 *
 * Every branch targets `resolveDrawerUpdateEntryId(originatingEntryId,
 * currentlySelectedEntryId)`, never `currentlySelectedEntryId` directly —
 * see that function's doc comment for why. This was audited across every
 * field DetailsDrawer can dispatch (folder, entryDate, mood, cover
 * set/remove, attachments-adjacent cover assignment): mood is the only one
 * with a deferred/teardown dispatch path today (its debounce timer and its
 * unmount-flush), but all of them are bound the same way here so none of
 * them can regress into the same defect if a future change adds a
 * debounce/deferral to any other field.
 */
export function dispatchDrawerUpdate(
  originatingEntryId: number,
  data: DiaryEntryUpdateData,
  currentlySelectedEntryId: number | null,
  handlers: DrawerUpdateHandlers,
): void {
  const targetId = resolveDrawerUpdateEntryId(originatingEntryId, currentlySelectedEntryId);

  if ("folderId" in data || "clearFolder" in data) {
    handlers.moveEntryToFolder(targetId, data.folderId ?? null);
    return;
  }
  if ("entryDate" in data) {
    handlers.updateEntry(targetId, data, "Failed to update the entry date.");
    return;
  }
  if ("mood" in data || "clearMood" in data) {
    handlers.updateEntry(targetId, data, "Failed to update mood.");
    return;
  }
  if ("clearCover" in data) {
    handlers.updateEntry(targetId, data, "Failed to remove cover image.");
    return;
  }
  if ("coverAttachmentId" in data) {
    handlers.updateEntry(targetId, data, "Failed to set cover image.");
    return;
  }
  handlers.updateEntry(targetId, data);
}
