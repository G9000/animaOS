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
  const { entryId, save, delayMs } = options;
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
      // Detach immediately so any schedule()/flush()/retry() call that
      // happens to fire during teardown (e.g. from a render still in
      // flight) can't reach an instance that's about to be disposed.
      schedulerRef.current = null;
      void scheduler.flush().finally(() => scheduler.dispose());
    };
    // `save` is intentionally excluded — see the doc comment above.
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
