import { useCallback, useEffect, useRef, useState } from "react";
import {
  createAutosaveScheduler,
  type AutosaveScheduler,
  type SaveStatus,
} from "../lib/autosaveScheduler";

export interface UseAutosaveOptions<T> {
  // Deviation from the brief's literal signature (which only lists `save`
  // and `delayMs`): a scheduler that never knows which entry it belongs to
  // cannot be recreated "when the entry id changes" as Step 1 requires, so
  // that identity has to come in as an explicit option. Pass `null` while
  // there is no entry to save against (e.g. before a new entry finishes
  // being created) — the hook still returns stable schedule/flush/retry
  // functions, they just have nothing to talk to yet.
  entryId: number | string | null;
  save: (payload: T) => Promise<void>;
  delayMs?: number;
  // Fires at most once per scheduler instance, from the teardown path
  // below, and only when a save that failed is STILL unsaved after this
  // component has already tried flush() and one retry() on the user's
  // behalf. By the time this fires the user may have already switched to
  // (or away from) the entry this scheduler belonged to, so the entry's
  // own per-entry status UI (PageHeader's saveStatus, keyed by entry.id)
  // can no longer be trusted to still be on screen — callers should
  // surface this somewhere that stays visible regardless of selection
  // (e.g. the workspace-level error banner), because the edit is now
  // genuinely gone: this scheduler is about to be disposed.
  onUnsavedOnTeardown?: () => void;
}

export interface UseAutosaveResult<T> {
  schedule: (payload: T) => void;
  flush: () => Promise<void>;
  retry: () => Promise<void>;
  status: SaveStatus;
}

/**
 * React binding over `createAutosaveScheduler`. The scheduler instance
 * lives in a ref (not state) so re-renders never recreate it; it is only
 * ever (re)created when `entryId` changes, and the effect cleanup flushes
 * then disposes the outgoing one.
 *
 * Why recreating only on `entryId` is safe even though `save`'s closure
 * changes every render: `save` is captured directly by the effect below,
 * at the moment the effect runs for a given `entryId` — not mirrored
 * through a ref that later renders keep overwriting. Its only capture that
 * actually varies across the lifetime of one entryId is the entryId
 * itself (title/body come in through the `schedule(payload)` argument, not
 * through closure), and that is exactly what's frozen for as long as this
 * effect doesn't re-run. So even though newer `save` closures get created
 * on every keystroke, none of them ever gets wired into this scheduler
 * instance — the one captured at creation time already points at the
 * right entry for its entire lifetime, and a save queued against it can
 * never be redirected to a different entry after the user switches pages
 * mid-debounce.
 */
export function useAutosave<T>(options: UseAutosaveOptions<T>): UseAutosaveResult<T> {
  const { entryId, save, delayMs, onUnsavedOnTeardown } = options;
  const [status, setStatus] = useState<SaveStatus>("idle");
  const schedulerRef = useRef<AutosaveScheduler<T> | null>(null);

  useEffect(() => {
    if (entryId == null) {
      schedulerRef.current = null;
      setStatus("idle");
      return;
    }

    const scheduler = createAutosaveScheduler<T>({
      delayMs,
      save,
      onStatusChange: setStatus,
    });
    schedulerRef.current = scheduler;
    setStatus("idle");

    return () => {
      // Fix round 1, Finding 3: this used to null `schedulerRef.current`
      // synchronously right here, before flush() even started awaiting.
      // That broke the caller's own "flush the entry being left before
      // deciding whether to discard it" logic on the true-unmount path:
      // the component's own unmount-cleanup effect calls the `flush`
      // wrapper below, which reads `schedulerRef.current` — if this
      // cleanup (registered earlier in hook order, since useAutosave is
      // called before that effect) had already nulled the ref, that call
      // would see `null` and resolve instantly, so the "flush first" step
      // was a no-op and a PATCH could race the subsequent DELETE.
      //
      // Deliberately NOT nulling the ref here: `schedulerRef.current` is
      // left pointing at this `scheduler` until either (a) this same
      // effect re-runs for a new `entryId` and overwrites it with a fresh
      // instance, in the same commit right after this cleanup — nothing
      // in between ever observes a null gap — or (b) the component fully
      // unmounts and the ref stops mattering. Any later call to
      // flush()/schedule()/retry() during teardown safely reaches this
      // real instance instead of null, and the scheduler's own
      // `disposed` guard (checked internally by every one of those
      // methods) is what makes calling them after dispose() has actually
      // completed a safe no-op — that guard was always the correct place
      // for this safety, not an external null-out.
      //
      // Finding 1 (PR #139): flush() alone is not enough. A save can have
      // already failed BEFORE teardown — createAutosaveScheduler keeps a
      // rejected payload in its private `failed` slot, not in `pending`,
      // specifically so a stale debounce timer never re-sends it on its
      // own (see retry()'s doc comment there). flush()'s drain loop only
      // ever consumes `pending`, so on its own it does nothing for a
      // payload sitting in `failed` — it would resolve immediately, this
      // scheduler would get disposed right after, and the user's last
      // edit would vanish with no save and no error, while reopening the
      // entry would show stale server content.
      //
      // The scheduler already has the right primitive for this: retry()
      // re-queues `failed` into `pending` and joins the exact same drain
      // loop flush() does. So: flush first (covers the common case where
      // nothing had failed), then if a failure is still outstanding, give
      // it exactly one retry() and flush() again. One attempt only — this
      // runs unconditionally on every entry switch/unmount, so retrying a
      // persistently-failing save here in a loop would hammer the server
      // every time the user navigates. If it fails again, the edit is
      // genuinely unsaveable right now; onUnsavedOnTeardown (checked
      // BEFORE dispose(), since invariant C silences onStatusChange after
      // it) lets the caller surface that rather than swallowing it.
      void (async () => {
        await scheduler.flush();
        if (scheduler.status() === "error") {
          await scheduler.retry();
          await scheduler.flush();
        }
        if (scheduler.status() === "error") {
          onUnsavedOnTeardown?.();
        }
        scheduler.dispose();
      })();
    };
    // `save` and `onUnsavedOnTeardown` are intentionally excluded — same
    // reasoning as the doc comment above for `save`: only the closures
    // captured at the moment this effect runs for a given `entryId` are
    // ever wired into this scheduler instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entryId, delayMs]);

  const schedule = useCallback((payload: T) => {
    schedulerRef.current?.schedule(payload);
  }, []);

  const flush = useCallback(async () => {
    await schedulerRef.current?.flush();
  }, []);

  const retry = useCallback(async () => {
    await schedulerRef.current?.retry();
  }, []);

  return { schedule, flush, retry, status };
}
