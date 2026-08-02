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
  let pending: { payload: T } | null = null;
  let inFlight: Promise<void> | null = null;
  let failed: { payload: T } | null = null;
  let status: SaveStatus = "idle";
  let disposed = false;

  const setStatus = (next: SaveStatus) => {
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

  async function drain(): Promise<void> {
    if (inFlight) {
      await inFlight;
      return;
    }
    while (pending !== null && !disposed) {
      const next = pending;
      pending = null;
      inFlight = run(next.payload);
      await inFlight;
      inFlight = null;
    }
  }

  return {
    schedule(payload: T) {
      if (disposed) return;
      pending = { payload };
      clearTimer();
      timer = setTimeout(() => {
        timer = null;
        void drain();
      }, delayMs);
    },
    async flush() {
      if (disposed) return;
      clearTimer();
      await drain();
      // A save that finished while another edit was queued leaves work
      // behind; drain again so flush is a real barrier.
      if (pending !== null) await drain();
    },
    async retry() {
      if (disposed || failed === null) return;
      const { payload } = failed;
      failed = null;
      inFlight = run(payload);
      await inFlight;
      inFlight = null;
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
