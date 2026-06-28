import { useCallback, useEffect, useMemo, useState } from "react";
import { getDaemonStatus, getDaemonLogs, restartDaemon, setDaemonBackground, setDaemonLock, startDaemon, stopDaemon, setDaemonControlToken } from "../../lib/daemon";
import type { DaemonStatusResponse } from "@anima/daemon-contracts";

const POLL_MS = 4000;
const LOG_LINES = 240;

type ActionState = "start" | "stop" | "restart" | "lock" | "unlock" | "background";

const STATE_CLASS: Record<DaemonStatusResponse["state"], string> = {
  stopped: "text-foreground/40 border-foreground/20",
  starting: "text-foreground/60 border-amber-400/60",
  ready: "text-emerald-300/80 border-emerald-300/55",
  degraded: "text-amber-300/80 border-amber-300/55",
  locked: "text-cyan-300/80 border-cyan-300/55",
  stopping: "text-blue-300/80 border-blue-300/55",
  failed: "text-destructive border-destructive/60",
};

const glass =
  "bg-background/25 backdrop-blur-[40px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.22)]";

interface DaemonSettingsProps {
  recoveryMode?: boolean;
}

export default function DaemonSettings({ recoveryMode = false }: DaemonSettingsProps) {
  const [status, setStatus] = useState<DaemonStatusResponse | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState<string | null>(null);
  const [isPolling, setIsPolling] = useState(true);
  const [busy, setBusy] = useState<ActionState | null>(null);
  const [tokenValue, setTokenValue] = useState("");
  const [logLines, setLogLines] = useState<string[]>([]);
  const [isLoadingLogs, setIsLoadingLogs] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const next = await getDaemonStatus();
      setStatus(next);
      setStatusError(null);
    } catch (error) {
      setStatus(null);
      setStatusError(error instanceof Error ? error.message : "Failed to query daemon status.");
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadStatus().catch(() => {});
    const interval = window.setInterval(async () => {
      if (!cancelled && isPolling) {
        await loadStatus();
      }
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [isPolling, loadStatus]);

  useEffect(() => {
    try {
      setTokenValue(localStorage.getItem("anima_daemon_control_token") ?? "");
    } catch {
      setTokenValue("");
    }
  }, []);

  const isBusy = useMemo(() => busy !== null, [busy]);

  const runAction = async (nextBusy: ActionState, action: () => Promise<void>, done: string, fail: string) => {
    setBusy(nextBusy);
    setActionMessage(null);
    try {
      await action();
      setActionMessage(done);
      await loadStatus();
    } catch (error) {
      setActionMessage(error instanceof Error ? `${fail}: ${error.message}` : fail);
    } finally {
      setBusy(null);
    }
  };

  const onLoadLogs = async () => {
    setIsLoadingLogs(true);
    try {
      const logs = await getDaemonLogs(LOG_LINES);
      setLogLines(logs.lines);
      setActionMessage(`Loaded ${logs.lines.length} log lines from ${logs.logFile}.`);
    } catch (error) {
      setActionMessage(error instanceof Error ? `Load logs failed: ${error.message}` : "Load logs failed.");
      setLogLines([]);
    } finally {
      setIsLoadingLogs(false);
    }
  };

  const stateClass = status ? STATE_CLASS[status.state] : "text-foreground/40 border-foreground/20";
  const lockButtonLabel = status?.lock.enabled ? "Unlock" : "Lock";
  const lockAction: ActionState = status?.lock.enabled ? "unlock" : "lock";
  const backgroundEnabled = status?.backgroundEnabled ?? false;
  const lockLabel = status?.lock.enabled ? "Locked" : "Unlocked";

  return (
    <div className="max-w-5xl p-2 space-y-4">
      <section className={`${glass} p-6 space-y-4`}>
        <h2 className="font-mono text-[9px] tracking-[0.22em] uppercase text-foreground/40">
          Local runtime daemon
        </h2>

        <p className="font-mono text-[11px] text-foreground/50 max-w-4xl leading-relaxed">
          {recoveryMode
            ? "Use this unauthenticated recovery surface to check daemon state and unlock or start the runtime when normal sign-in is unavailable."
            : "The daemon runs outside the desktop window, supervises the Python runtime, and exposes a local control surface on port "}
          {!recoveryMode ? <span className="text-foreground">3032</span> : null}
          {!recoveryMode ? "." : null}
        </p>

        <div className="h-px bg-foreground/[0.06]" />

        <div className="grid gap-3 sm:grid-cols-2">
          <button
            className={`border px-4 py-2 rounded-sm font-mono text-xs uppercase tracking-[0.14em] transition-all ${stateClass} hover:brightness-125`}
            onClick={() => setIsPolling((current) => !current)}
            type="button"
          >
            Polling: {isPolling ? "On" : "Paused"}
          </button>
          <button
            className="border border-foreground/20 px-4 py-2 rounded-sm font-mono text-xs uppercase tracking-[0.14em] hover:brightness-125"
            onClick={() => loadStatus()}
            type="button"
          >
            Refresh once
          </button>
        </div>

        {status ? (
          <div className="text-sm font-mono text-foreground/70 space-y-2">
            <p className="inline-flex items-center gap-2">
              <span className="text-xs uppercase tracking-[0.16em] opacity-75">State:</span>
              <span className={`border px-2 py-0.5 rounded-sm ${stateClass}`}>{status.state}</span>
              <span className="text-xs opacity-60">({lockLabel})</span>
            </p>
            <p>
              Runtime: {status.runtime.port} | PID {status.runtime.pid ?? "not running"} |
              Artifacts: {status.runtime.artifactPath}
            </p>
            <p>Launch mode: {status.runtime.launchMode}</p>
            <p>Background mode: {backgroundEnabled ? "enabled" : "disabled"}</p>
            <p>Restart policy: attempt {status.restart.attempts}/{status.restart.maxAttempts}, next backoff {status.restart.nextDelaySeconds}s</p>
            {status.error ? <p className="text-destructive">Runtime error: {status.error}</p> : null}
          </div>
        ) : (
          <p className="font-mono text-xs text-foreground/70">
            {statusError ? `Could not load daemon status: ${statusError}` : "Waiting for daemon response..."}
          </p>
        )}
      </section>

      <section className={`${glass} p-6 space-y-4`}>
        <h3 className="font-mono text-[9px] tracking-[0.22em] uppercase text-foreground/40">
          Control
        </h3>

        <div className={`grid gap-2 ${recoveryMode ? "sm:grid-cols-2" : "sm:grid-cols-3"}`}>
          <button
            className="border border-foreground/[0.14] px-4 py-2 rounded-sm text-sm hover:bg-foreground/8 disabled:opacity-50"
            onClick={() => runAction("start", () => startDaemon(), "Runtime start requested.", "Start failed")}
            disabled={isBusy || status?.state === "starting" || status?.state === "ready"}
            type="button"
          >
            Start
          </button>
          {recoveryMode ? null : (
            <>
              <button
                className="border border-foreground/[0.14] px-4 py-2 rounded-sm text-sm hover:bg-foreground/8 disabled:opacity-50"
                onClick={() => runAction("restart", () => restartDaemon(), "Runtime restart requested.", "Restart failed")}
                disabled={isBusy || !status}
                type="button"
              >
                Restart
              </button>
              <button
                className="border border-destructive/30 px-4 py-2 rounded-sm text-sm hover:bg-destructive/10 disabled:opacity-50"
                onClick={() => runAction("stop", () => stopDaemon(), "Runtime stop requested.", "Stop failed")}
                disabled={isBusy}
                type="button"
              >
                Stop
              </button>
            </>
          )}
        </div>

        <div className="flex flex-wrap gap-2 items-center">
          <button
            className="border border-cyan-300/30 px-4 py-2 rounded-sm text-sm hover:bg-cyan-300/8 disabled:opacity-50"
            onClick={() => runAction(
              recoveryMode ? "unlock" : lockAction,
              () => setDaemonLock(recoveryMode ? false : !status?.lock.enabled),
              recoveryMode || status?.lock.enabled ? "Runtime unlocked." : "Runtime locked.",
              recoveryMode || status?.lock.enabled ? "Unlock failed" : "Lock failed",
            )}
            disabled={isBusy || !status || (recoveryMode && !status.lock.enabled)}
            type="button"
          >
            {recoveryMode ? "Unlock runtime" : lockButtonLabel}
          </button>
          {recoveryMode ? null : (
            <button
              className="border border-foreground/[0.14] px-4 py-2 rounded-sm text-sm hover:bg-foreground/8 disabled:opacity-50"
              onClick={() => runAction("background", () => setDaemonBackground(!backgroundEnabled), "Background mode updated.", "Background update failed")}
              disabled={isBusy || !status}
              type="button"
            >
              {backgroundEnabled ? "Disable background" : "Enable background"}
            </button>
          )}

          {recoveryMode ? null : (
            <button
              className="border border-foreground/[0.14] px-4 py-2 rounded-sm text-sm hover:bg-foreground/8 disabled:opacity-50"
              onClick={onLoadLogs}
              disabled={isBusy || isLoadingLogs}
              type="button"
            >
              {isLoadingLogs ? "Loading logs..." : "Open logs"}
            </button>
          )}
        </div>

        <label className="space-y-2 block">
          <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-foreground/60">
            Daemon control token (localStorage)
          </span>
          <input
            value={tokenValue}
            onChange={(event) => {
              const value = event.target.value;
              setTokenValue(value);
              setDaemonControlToken(value);
            }}
            className="w-full border border-foreground/[0.16] bg-background/25 px-3 py-2 font-mono text-xs outline-none"
            placeholder="Optional for token-guarded daemon mode"
          />
        </label>

        {actionMessage ? (
          <p className="text-sm font-mono text-foreground/65">{actionMessage}</p>
        ) : null}
      </section>

      {recoveryMode ? null : (
        <section className={`${glass} p-6 space-y-3`}>
          <h3 className="font-mono text-[9px] tracking-[0.22em] uppercase text-foreground/40">
            Runtime diagnostics
          </h3>
          <pre className="max-h-64 overflow-auto bg-background/55 border border-foreground/[0.08] p-3 text-xs font-mono">
            {logLines.length ? logLines.join("\n") : "No logs loaded."}
          </pre>
        </section>
      )}
    </div>
  );
}

