export type SaveStatus = "idle" | "saving" | "saved" | "error";

export interface AutosaveScheduler<T> {
  schedule(payload: T): void;
  flush(): Promise<void>;
  retry(): Promise<void>;
  status(): SaveStatus;
  dispose(): void;
}

export interface AutosaveSchedulerOptions<T> {
  save: (payload: T) => Promise<void>;
  delayMs?: number;
  onStatusChange?: (status: SaveStatus) => void;
}

export function createAutosaveScheduler<T>(
  options: AutosaveSchedulerOptions<T>,
): AutosaveScheduler<T> {
  const delayMs = options.delayMs ?? 800;

  let timer: ReturnType<typeof setTimeout> | null = null;
  // The latest payload waiting to be sent. schedule() and a superseded
  // retry() both just overwrite this — only the newest payload is ever
  // sent, and this is also how the loop below knows there is more work.
  let pending: { payload: T } | null = null;
  // Non-null exactly while the single drain loop below is active. This is
  // the ONE thing every entry point (the debounce timer, flush, retry)
  // awaits, so "waiting on the loop" always means the same promise object
  // to everyone — there is no separate re-check of `pending` after the
  // fact that could race the loop's own continuation.
  let loopPromise: Promise<void> | null = null;
  let failed: { payload: T } | null = null;
  let status: SaveStatus = "idle";
  let disposed = false;

  const setStatus = (next: SaveStatus) => {
    // Once disposed, never invoke the callback again — a save that was
    // already in flight when dispose() was called may still settle
    // afterward, and callers (e.g. a React effect) may have already torn
    // down whatever this callback would update.
    if (disposed) return;
    if (status === next) return;
    status = next;
    options.onStatusChange?.(next);
  };

  const clearTimer = () => {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };

  async function run(payload: T): Promise<void> {
    setStatus("saving");
    try {
      await options.save(payload);
      failed = null;
      // A newer edit arrived mid-flight; it is still pending, so do not
      // claim "saved" yet.
      if (pending === null) setStatus("saved");
    } catch {
      failed = { payload };
      setStatus("error");
    }
  }

  // The single owner of every options.save() call. Its while-condition is
  // `pending !== null`, so it can never exit (leave loopPromise non-null)
  // while there is unsent work. That means anything that awaits the
  // promise this returns is guaranteed, once it resolves, that no save is
  // in flight AND pending is null — a real barrier, satisfied uniformly
  // whether the caller is the debounce timer, flush(), or retry().
  //
  // ORDERING HAZARD — do not remove the leading `await Promise.resolve()`
  // below. `loopPromise = runLoop()` only works if `runLoop`'s body
  // cannot reach its final `loopPromise = null` line before that
  // assignment itself happens. An async function body runs SYNCHRONOUSLY
  // up to its first `await`; if `pending` is already null when this is
  // called, a loop body with no leading await would run its while
  // condition (false), skip straight to `loopPromise = null`, and return
  // — all *before* `loopPromise = runLoop()` gets a chance to run, since
  // that assignment only happens once the call expression finishes
  // evaluating. The correct `null` reset would then get immediately
  // clobbered by the outer assignment writing a stray non-null (already
  // resolved) promise over it, permanently "poisoning" loopPromise: every
  // later schedule()/flush()/retry() would see it as truthy and silently
  // no-op forever. Forcing a suspend before the loop body touches any
  // shared state guarantees `loopPromise = runLoop()` always completes
  // first, no matter how little (or how much) work the loop ends up
  // doing.
  const runLoop = async (): Promise<void> => {
    await Promise.resolve();
    while (!disposed && pending !== null) {
      const next = pending;
      pending = null;
      await run(next.payload);
    }
    loopPromise = null;
  };

  function ensureLoop(): Promise<void> {
    if (loopPromise) return loopPromise;
    loopPromise = runLoop();
    return loopPromise;
  }

  return {
    schedule(payload: T) {
      if (disposed) return;
      pending = { payload };
      // A new, unsent edit just landed — status must not keep reporting
      // "saved" for the whole debounce window while it sits unsent
      // (invariant D). "idle" already exists in SaveStatus for exactly
      // this "no active save, but not a clean confirmed save either"
      // case, so no new state is needed.
      if (status === "saved") setStatus("idle");
      clearTimer();
      timer = setTimeout(() => {
        timer = null;
        void ensureLoop();
      }, delayMs);
    },
    async flush() {
      if (disposed) return;
      clearTimer();
      // Join the (possibly already-running) loop. Because it never exits
      // while `pending !== null`, this single await is sufficient: it
      // covers both "nothing was pending" (the loop resolves immediately)
      // and "an edit landed mid-flight" (the loop picks it up and keeps
      // going before resolving).
      await ensureLoop();
    },
    async retry() {
      if (disposed || failed === null) return;
      const { payload } = failed;
      failed = null;
      // A newer edit already queued always wins over the stale failed
      // payload; otherwise re-queue the failed payload so it runs through
      // the same shared loop instead of starting a second, concurrent
      // save.
      if (pending === null) pending = { payload };
      await ensureLoop();
    },
    status() {
      return status;
    },
    dispose() {
      disposed = true;
      clearTimer();
      pending = null;
    },
  };
}
