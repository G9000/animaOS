// Round 6 fix (P2, regression from round 5 — commit 152ca0e3): round 5's
// `onUploadOrphaned` notice (see DiaryImageOptions in
// ../editor/nodes/AttachmentImage.tsx) called `setError` and `reload` on
// whatever DiaryWorkspace hook state happened to be alive at the moment an
// orphaned upload resolved. That works when the user merely switched
// entries (DiaryWorkspace itself stays mounted) but is silently lost when
// the user leaves `/journal` entirely first — DiaryWorkspace has already
// unmounted, so `setError`/`reload` target disposable hook state that
// nothing is listening to anymore, and the whole point of round 5's fix
// (making a downgrade from inline image to plain attachment visible) is
// defeated in what is arguably the more common case.
//
// Fix: a module-scoped, in-memory queue that OUTLIVES any single
// DiaryWorkspace mount but dies with the page (a full reload). That is
// exactly the right lifetime here — a route change away from `/journal`
// and back must still show the notice; a fresh app launch correctly does
// not, because there is nothing left to be silent about. This deliberately
// does NOT use localStorage/sessionStorage/IndexedDB: even though a notice
// string isn't diary CONTENT, it names user activity, and this codebase's
// no-browser-storage rule for diary-related state is honored strictly
// rather than argued around (see DiaryWorkspace.tsx's legacy-draft purge
// effect for the same rule applied to actual content).
//
// Coalescing choice: multiple notices queued while unmounted are collapsed
// into ONE message with a count, not shown one-by-one or as separate error
// banners. DiaryWorkspace's error slot is a single string (`error` state,
// one banner), not a list, and firing `setError` N times in a row would
// just have each call overwrite the last — the user would only ever see
// the LAST orphaned upload, silently dropping any count of everything
// before it, in the same "downgrade goes quiet" family of bug this fix
// exists to close. Coalescing into a single "N images…" message is the
// only option here that keeps every queued notice visible.
export interface OrphanUploadNotice {
  entryId: number;
  attachmentId: number;
}

type Listener = () => void;

let pending: OrphanUploadNotice[] = [];
let listener: Listener | null = null;

// Called by DiaryWorkspace's onUploadOrphaned handler, mounted or not — the
// caller never needs to know whether a workspace is currently alive to
// receive it. If one IS alive and subscribed, its listener fires
// synchronously so the notice shows without waiting for a remount.
export function queueOrphanUploadNotice(notice: OrphanUploadNotice): void {
  pending.push(notice);
  listener?.();
}

// Drains whatever is queued (idempotent: a second call with nothing newly
// queued returns null, never re-shows the same notice) and returns a single
// coalesced message, or null if there was nothing to show.
export function drainOrphanUploadNotices(): string | null {
  if (pending.length === 0) return null;
  const count = pending.length;
  pending = [];
  return count === 1
    ? "An image finished uploading after you left the entry. It was saved as an attachment instead of appearing inline."
    : `${count} images finished uploading after you left the entry. They were saved as attachments instead of appearing inline.`;
}

// Subscribes the currently-mounted DiaryWorkspace to be notified the
// instant a new notice is queued (the "still in /journal, just switched
// entries" case, which already worked before this fix — kept working
// through the same mechanism so there is only one code path, not two).
// Only one subscriber at a time makes sense here (there is only ever one
// DiaryWorkspace mounted); a later subscribe replaces the current one, and
// unsubscribing is a no-op if a newer subscriber already took over.
export function subscribeOrphanUploadNotices(fn: Listener): () => void {
  listener = fn;
  return () => {
    if (listener === fn) listener = null;
  };
}

// Test-only reset — this module's state is otherwise process-lifetime.
export function __resetOrphanUploadNoticesForTest(): void {
  pending = [];
  listener = null;
}
