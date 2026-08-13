import { useEffect, useState } from "react";
import type {
  CoreActiveStatus,
  CoreArchivePayloadKind,
  CoreImportOperation,
  CoreImportProbe,
  CoreFsRecoveryBrowseResponse,
  CoreTransferDestinationProbe,
  CoreTransferEstimate,
  CoreTransferOperation,
} from "@anima/api-client";
import { glass } from "@anima/standard-templates";
import { api } from "../../lib/api";

const INPUT_CLASS =
  "w-full bg-foreground/[0.04] border border-hairline px-3 py-2 text-sm text-foreground placeholder:text-foreground/25 outline-none focus:border-hairline-strong transition-colors font-mono";

const PAYLOADS: Array<{
  kind: CoreArchivePayloadKind;
  label: string;
  description: string;
}> = [
  {
    kind: "full",
    label: "Full ANIMA CORE",
    description: "Soul, CoreFS, wrapped keyslots, and recovery material. Runtime is excluded.",
  },
  {
    kind: "soul",
    label: "Soul only",
    description: "Advanced recovery. Restores in filesystem-missing degraded mode.",
  },
  {
    kind: "fs",
    label: "CoreFS only",
    description: "Advanced recovery/export mode. V1 reattachment is not supported.",
  },
];

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "Unknown";
  if (value < 1024) return `${value} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let amount = value / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && amount >= 1024; index += 1) {
    amount /= 1024;
    unit = units[index];
  }
  return `${amount.toFixed(amount >= 10 ? 1 : 2)} ${unit}`;
}

export default function CoreTransferSettings() {
  const [payloadKind, setPayloadKind] = useState<CoreArchivePayloadKind>("full");
  const [destination, setDestination] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [estimate, setEstimate] = useState<CoreTransferEstimate | null>(null);
  const [probe, setProbe] = useState<CoreTransferDestinationProbe | null>(null);
  const [operation, setOperation] = useState<CoreTransferOperation | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [archivePath, setArchivePath] = useState("");
  const [stagingParent, setStagingParent] = useState("");
  const [importPassphrase, setImportPassphrase] = useState("");
  const [importProbe, setImportProbe] = useState<CoreImportProbe | null>(null);
  const [importOperation, setImportOperation] = useState<CoreImportOperation | null>(null);
  const [importBusy, setImportBusy] = useState(false);
  const [importStatus, setImportStatus] = useState("");
  const [activeCoreStatus, setActiveCoreStatus] = useState<CoreActiveStatus | null>(null);
  const [rollbackConfirmed, setRollbackConfirmed] = useState(false);
  const [rollbackBusy, setRollbackBusy] = useState(false);
  const [rollbackStatus, setRollbackStatus] = useState("");
  const [recoveryCredentialKind, setRecoveryCredentialKind] = useState<"password" | "recovery">("recovery");
  const [recoveryCredential, setRecoveryCredential] = useState("");
  const [recoveryBrowseOperation, setRecoveryBrowseOperation] = useState<"stat" | "list" | "read">("list");
  const [recoveryBrowsePath, setRecoveryBrowsePath] = useState("");
  const [recoveryBrowseResult, setRecoveryBrowseResult] = useState<CoreFsRecoveryBrowseResponse | null>(null);
  const [recoveryBrowseBusy, setRecoveryBrowseBusy] = useState(false);
  const [recoveryBrowseStatus, setRecoveryBrowseStatus] = useState("");

  useEffect(() => {
    let active = true;
    void api.corefs.transfer
      .activeCore()
      .then((value) => {
        if (active) setActiveCoreStatus(value);
      })
      .catch(() => {
        // Startup may still be initializing the authenticated registry.
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    setEstimate(null);
    setProbe(null);
    void api.corefs.transfer
      .estimate(payloadKind)
      .then((value) => {
        if (active) setEstimate(value);
      })
      .catch((error: unknown) => {
        if (active) setStatus(error instanceof Error ? error.message : "Transfer estimate unavailable.");
      });
    return () => {
      active = false;
    };
  }, [payloadKind]);

  const handleProbe = async () => {
    if (!destination.trim()) {
      setStatus("Choose an existing local destination folder.");
      return;
    }
    setBusy(true);
    setStatus("");
    try {
      const value = await api.corefs.transfer.probe(destination.trim(), payloadKind);
      setProbe(value);
      setEstimate(value);
      if (value.publicationMode === "multipart") {
        setStatus(
          `This volume needs ${value.declaredVolumeCount} authenticated parts. Multipart export remains disabled until its native volume-set gate is complete.`,
        );
      } else {
        setStatus("Destination passed capacity, writable-file, directory, and atomic-rename probes.");
      }
    } catch (error) {
      setProbe(null);
      setStatus(error instanceof Error ? error.message : "Destination probe failed.");
    } finally {
      setBusy(false);
    }
  };

  const pollOperation = async (operationId: string) => {
    for (;;) {
      const current = await api.corefs.transfer.operation(operationId);
      setOperation(current);
      if (["completed", "cancelled", "failed"].includes(current.state)) return current;
      await new Promise((resolve) => window.setTimeout(resolve, 350));
    }
  };

  const handleExport = async () => {
    if (!probe || probe.destination !== destination.trim()) {
      setStatus("Probe this exact destination before export.");
      return;
    }
    if (probe.publicationMode !== "single_file") {
      setStatus("This destination requires multipart export, which is still gated.");
      return;
    }
    if (passphrase.length < 8) {
      setStatus("Archive passphrase must be at least 8 characters.");
      return;
    }
    setBusy(true);
    setStatus("");
    try {
      const started = await api.corefs.transfer.prepare({
        destination: probe.destination,
        passphrase,
        payloadKind,
      });
      setOperation(started);
      const completed = await pollOperation(started.operationId);
      if (completed.state === "completed") {
        setStatus(`Verified ANIMA CORE published safely to ${completed.resultPath}.`);
      } else if (completed.state === "cancelled") {
        setStatus("Export cancelled; unpublished partial output was removed.");
      } else {
        setStatus("Export failed closed; no unverified final artifact was published.");
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "ANIMA CORE export failed.");
    } finally {
      setBusy(false);
      setPassphrase("");
    }
  };

  const handleCancel = async () => {
    if (!operation || ["completed", "cancelled", "failed"].includes(operation.state)) return;
    try {
      setOperation(await api.corefs.transfer.cancel(operation.operationId));
      setStatus("Cancellation requested. The current bounded native chunk will finish safely.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Cancellation request failed.");
    }
  };

  const handleImportProbe = async () => {
    if (!archivePath.trim() || !stagingParent.trim()) {
      setImportStatus("Choose an archive file and an existing same-volume staging folder.");
      return;
    }
    setImportBusy(true);
    setImportStatus("");
    try {
      const value = await api.corefs.transfer.probeImport(
        archivePath.trim(),
        stagingParent.trim(),
      );
      setImportProbe(value);
      setImportStatus("Import staging passed source, capacity, and same-volume safety checks.");
    } catch (error) {
      setImportProbe(null);
      setImportStatus(error instanceof Error ? error.message : "Import staging probe failed.");
    } finally {
      setImportBusy(false);
    }
  };

  const pollImport = async (operationId: string) => {
    for (;;) {
      const current = await api.corefs.transfer.importOperation(operationId);
      setImportOperation(current);
      if (["completed", "cancelled", "failed"].includes(current.state)) return current;
      await new Promise((resolve) => window.setTimeout(resolve, 350));
    }
  };

  const handleImport = async () => {
    if (!importProbe || importProbe.stagingParent !== stagingParent.trim()) {
      setImportStatus("Probe this exact archive and staging folder before restore.");
      return;
    }
    if (importPassphrase.length < 8) {
      setImportStatus("Archive passphrase must be at least 8 characters.");
      return;
    }
    setImportBusy(true);
    setImportStatus("");
    try {
      const started = await api.corefs.transfer.prepareImport({
        archivePath: archivePath.trim(),
        stagingParent: importProbe.stagingParent,
        passphrase: importPassphrase,
      });
      setImportOperation(started);
      const completed = await pollImport(started.operationId);
      if (completed.state === "completed") {
        setImportStatus(
          completed.recoveryState === "complete"
            ? "Archive authenticated and staged. Activation remains restart-gated; the running Core was not changed."
            : `Archive authenticated and staged in ${completed.recoveryState} mode. Scoped credential activation is still required.`,
        );
      } else if (completed.state === "cancelled") {
        setImportStatus("Restore staging cancelled; partial extraction was removed.");
      } else {
        setImportStatus("Restore staging failed closed; the running Core was not changed.");
      }
    } catch (error) {
      setImportStatus(error instanceof Error ? error.message : "ANIMA CORE restore staging failed.");
    } finally {
      setImportBusy(false);
      setImportPassphrase("");
    }
  };

  const handleImportCancel = async () => {
    if (!importOperation || ["completed", "cancelled", "failed"].includes(importOperation.state)) {
      return;
    }
    try {
      setImportOperation(await api.corefs.transfer.cancelImport(importOperation.operationId));
      setImportStatus("Restore cancellation requested; staged residue will be removed safely.");
    } catch (error) {
      setImportStatus(error instanceof Error ? error.message : "Restore cancellation failed.");
    }
  };

  const handleScheduleActivation = async () => {
    if (
      !importOperation ||
      importOperation.state !== "completed" ||
      importOperation.payloadKind !== "full"
    ) {
      return;
    }
    setImportBusy(true);
    try {
      const scheduled = await api.corefs.transfer.activateImportOnRestart(
        importOperation.operationId,
      );
      setImportOperation(scheduled);
      setImportStatus(
        "Activation is authenticated and scheduled. Restart animaOS to switch Cores; the current Core remains active until shutdown.",
      );
    } catch (error) {
      setImportStatus(error instanceof Error ? error.message : "Activation scheduling failed.");
    } finally {
      setImportBusy(false);
    }
  };

  const handleScheduleRollback = async () => {
    if (!activeCoreStatus?.retainedCoreId || !rollbackConfirmed) return;
    setRollbackBusy(true);
    setRollbackStatus("");
    try {
      const scheduled = await api.corefs.transfer.rollbackOnRestart();
      setActiveCoreStatus(scheduled);
      setRollbackConfirmed(false);
      setRollbackStatus(
        "Rollback is authenticated and scheduled. The current Core remains active until animaOS restarts.",
      );
    } catch (error) {
      setRollbackStatus(error instanceof Error ? error.message : "Rollback scheduling failed.");
    } finally {
      setRollbackBusy(false);
    }
  };

  const handleRecoveryBrowse = async () => {
    if (
      !importOperation ||
      importOperation.state !== "completed" ||
      importOperation.recoveryState !== "recovery_only"
    ) {
      return;
    }
    if (!recoveryCredential) {
      setRecoveryBrowseStatus("Enter the CoreFS password or recovery phrase for this staged archive.");
      return;
    }
    setRecoveryBrowseBusy(true);
    setRecoveryBrowseStatus("");
    try {
      const result = await api.corefs.transfer.browseCoreFsRecovery(
        importOperation.operationId,
        {
          operation: recoveryBrowseOperation,
          credentialKind: recoveryCredentialKind,
          credential: recoveryCredential,
          path: recoveryBrowsePath.trim(),
        },
      );
      setRecoveryBrowseResult(result);
      setRecoveryBrowseStatus(
        `Authenticated read-only CoreFS snapshot ${result.generation}. The staged Core was not attached or activated.`,
      );
    } catch (error) {
      setRecoveryBrowseResult(null);
      setRecoveryBrowseStatus(
        error instanceof Error ? error.message : "CoreFS recovery browse failed closed.",
      );
    } finally {
      setRecoveryCredential("");
      setRecoveryBrowseBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <section className={`${glass} p-6 space-y-5`}>
        <div>
          <h2 className="font-mono text-label tracking-caps-4 uppercase text-foreground/50">
            Export ANIMA CORE
          </h2>
          <p className="mt-2 font-mono text-caption text-foreground/35 leading-relaxed">
            Create a verified local encrypted transfer artifact. Runtime databases, device configuration,
            logs, caches, and OS credentials are never included.
          </p>
        </div>

        <div className="grid gap-2 md:grid-cols-3">
          {PAYLOADS.map((payload) => (
            <button
              key={payload.kind}
              type="button"
              onClick={() => setPayloadKind(payload.kind)}
              className={`border p-3 text-left transition-colors ${
                payloadKind === payload.kind
                  ? "border-foreground/45 bg-foreground/[0.08]"
                  : "border-hairline hover:border-hairline-strong"
              }`}
            >
              <span className="block font-mono text-label uppercase tracking-caps-2 text-foreground/65">
                {payload.label}
              </span>
              <span className="mt-1 block font-mono text-micro leading-relaxed text-foreground/30">
                {payload.description}
              </span>
            </button>
          ))}
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <Metric label="Selected" value={formatBytes(estimate?.selectedBytes)} />
          <Metric label="Required free space" value={formatBytes(estimate?.requiredCapacityBytes)} />
          <Metric label="Soul checkpoint" value={estimate?.soulGeneration?.toString() ?? "Not included"} />
          <Metric
            label="Filesystem checkpoint"
            value={estimate?.filesystemGeneration?.toString() ?? "Not included"}
          />
        </div>

        <div className="space-y-1.5">
          <label className="font-mono text-label tracking-caps-3 uppercase text-foreground/35">
            Local destination folder
          </label>
          <div className="flex gap-2">
            <input
              value={destination}
              onChange={(event) => {
                setDestination(event.target.value);
                setProbe(null);
              }}
              className={INPUT_CLASS}
              placeholder="Existing local or removable-media folder"
            />
            <ActionButton onClick={handleProbe} disabled={busy}>Probe</ActionButton>
          </div>
        </div>

        {probe && (
          <div className="grid gap-3 md:grid-cols-3">
            <Metric label="Available" value={formatBytes(probe.availableBytes)} />
            <Metric label="Single-file limit" value={formatBytes(probe.maximumSingleFileBytes)} />
            <Metric
              label="Publication"
              value={
                probe.publicationMode === "single_file"
                  ? "Single verified file"
                  : `${probe.declaredVolumeCount} authenticated parts`
              }
            />
          </div>
        )}

        <div className="space-y-1.5">
          <label className="font-mono text-label tracking-caps-3 uppercase text-foreground/35">
            Archive passphrase
          </label>
          <input
            type="password"
            value={passphrase}
            onChange={(event) => setPassphrase(event.target.value)}
            className={INPUT_CLASS}
            placeholder="Used only in memory for this export"
            autoComplete="new-password"
          />
        </div>

        <div className="flex flex-wrap gap-2">
          <ActionButton onClick={handleExport} disabled={busy || !probe}>
            {busy ? "Working…" : "Export ANIMA CORE"}
          </ActionButton>
          {operation && !["completed", "cancelled", "failed"].includes(operation.state) && (
            <ActionButton onClick={handleCancel}>Cancel safely</ActionButton>
          )}
        </div>

        {operation && (
          <div className="space-y-2">
            <div className="h-1.5 bg-foreground/[0.08] overflow-hidden">
              <div
                className="h-full bg-foreground/55 transition-[width]"
                style={{ width: `${operation.progressPercent}%` }}
              />
            </div>
            <p className="font-mono text-micro uppercase tracking-caps-2 text-foreground/35">
              {operation.phase} · {operation.progressPercent}% · {formatBytes(operation.bytesPublished)} published
            </p>
          </div>
        )}

        {status && <p className="font-mono text-caption text-foreground/45 leading-relaxed">{status}</p>}
      </section>

      <section className={`${glass} p-6 space-y-5`}>
        <h2 className="font-mono text-label tracking-caps-4 uppercase text-foreground/50">
          Restore ANIMA CORE
        </h2>
        <p className="font-mono text-caption text-foreground/35 leading-relaxed">
          Authenticate and extract an archive into a verified same-volume staging Core. This step never changes the
          running Core or opens an archive in place; activation and retained-Core rollback remain restart-gated.
        </p>

        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-1.5">
            <label className="font-mono text-label tracking-caps-3 uppercase text-foreground/35">
              Archive file
            </label>
            <input
              value={archivePath}
              onChange={(event) => {
                setArchivePath(event.target.value);
                setImportProbe(null);
              }}
              className={INPUT_CLASS}
              placeholder="Local .anima archive file"
            />
          </div>
          <div className="space-y-1.5">
            <label className="font-mono text-label tracking-caps-3 uppercase text-foreground/35">
              Same-volume staging folder
            </label>
            <input
              value={stagingParent}
              onChange={(event) => {
                setStagingParent(event.target.value);
                setImportProbe(null);
              }}
              className={INPUT_CLASS}
              placeholder="Existing folder beside the future Core"
            />
          </div>
        </div>

        {importProbe && (
          <div className="grid gap-3 md:grid-cols-3">
            <Metric label="Archive" value={formatBytes(importProbe.archiveBytes)} />
            <Metric label="Available" value={formatBytes(importProbe.availableBytes)} />
            <Metric label="Required staging" value={formatBytes(importProbe.requiredCapacityBytes)} />
          </div>
        )}

        <div className="space-y-1.5">
          <label className="font-mono text-label tracking-caps-3 uppercase text-foreground/35">
            Archive passphrase
          </label>
          <input
            type="password"
            value={importPassphrase}
            onChange={(event) => setImportPassphrase(event.target.value)}
            className={INPUT_CLASS}
            placeholder="Used only in memory while authenticating the archive"
            autoComplete="new-password"
          />
        </div>

        <div className="flex flex-wrap gap-2">
          <ActionButton onClick={handleImportProbe} disabled={importBusy}>Probe import</ActionButton>
          <ActionButton onClick={handleImport} disabled={importBusy || !importProbe}>
            {importBusy ? "Working…" : "Verify and stage restore"}
          </ActionButton>
          {importOperation && !["completed", "cancelled", "failed"].includes(importOperation.state) && (
            <ActionButton onClick={handleImportCancel}>Cancel staging</ActionButton>
          )}
          {importOperation?.state === "completed" &&
            importOperation.payloadKind === "full" &&
            !importOperation.restartRequired && (
              <ActionButton onClick={handleScheduleActivation} disabled={importBusy}>
                Activate on restart
              </ActionButton>
            )}
        </div>

        {importOperation && (
          <div className="space-y-2">
            <div className="h-1.5 bg-foreground/[0.08] overflow-hidden">
              <div
                className="h-full bg-foreground/55 transition-[width]"
                style={{ width: `${importOperation.progressPercent}%` }}
              />
            </div>
            <p className="font-mono text-micro uppercase tracking-caps-2 text-foreground/35">
              {importOperation.phase} · {importOperation.progressPercent}% · {formatBytes(importOperation.bytesProcessed)} verified
            </p>
          </div>
        )}

        {importStatus && (
          <p className="font-mono text-caption text-foreground/45 leading-relaxed">{importStatus}</p>
        )}

        {importOperation?.state === "completed" &&
          importOperation.recoveryState === "recovery_only" && (
            <div className="border-t border-hairline pt-5 space-y-4">
              <div>
                <h3 className="font-mono text-label tracking-caps-3 uppercase text-foreground/45">
                  Browse recovered CoreFS
                </h3>
                <p className="mt-1 font-mono text-micro text-foreground/30 leading-relaxed">
                  Authenticate one bounded read against the staged recovery Core. Credentials stay
                  in memory for this request only; browsing never attaches the filesystem to a Soul.
                </p>
              </div>

              <div className="grid gap-3 md:grid-cols-3">
                <select
                  value={recoveryCredentialKind}
                  onChange={(event) => setRecoveryCredentialKind(event.target.value as "password" | "recovery")}
                  className={INPUT_CLASS}
                >
                  <option value="recovery">Recovery phrase</option>
                  <option value="password">CoreFS password</option>
                </select>
                <select
                  value={recoveryBrowseOperation}
                  onChange={(event) => setRecoveryBrowseOperation(event.target.value as "stat" | "list" | "read")}
                  className={INPUT_CLASS}
                >
                  <option value="list">List folder</option>
                  <option value="stat">Inspect path</option>
                  <option value="read">Read bounded chunk</option>
                </select>
                <input
                  value={recoveryBrowsePath}
                  onChange={(event) => setRecoveryBrowsePath(event.target.value)}
                  className={INPUT_CLASS}
                  placeholder="CoreFS path; blank means root"
                />
              </div>

              <input
                type="password"
                value={recoveryCredential}
                onChange={(event) => setRecoveryCredential(event.target.value)}
                className={INPUT_CLASS}
                placeholder="Used only for this read-only recovery request"
                autoComplete="off"
              />

              <ActionButton onClick={handleRecoveryBrowse} disabled={recoveryBrowseBusy}>
                {recoveryBrowseBusy ? "Authenticating…" : "Browse staged CoreFS"}
              </ActionButton>

              {recoveryBrowseResult && (
                <pre className="max-h-64 overflow-auto border border-hairline bg-foreground/[0.03] p-3 font-mono text-micro text-foreground/50 whitespace-pre-wrap break-words">
                  {JSON.stringify(recoveryBrowseResult.result, null, 2)}
                </pre>
              )}
              {recoveryBrowseStatus && (
                <p className="font-mono text-caption text-foreground/45 leading-relaxed">
                  {recoveryBrowseStatus}
                </p>
              )}
            </div>
          )}
      </section>

      {activeCoreStatus?.retainedCoreId && (
        <section className={`${glass} p-6 space-y-5`}>
          <div>
            <h2 className="font-mono text-label tracking-caps-4 uppercase text-foreground/50">
              Retained Core rollback
            </h2>
            <p className="mt-2 font-mono text-caption text-foreground/35 leading-relaxed">
              The prior authenticated Core is retained after activation. Rollback changes no live
              resources and can run only during the next pre-resource startup.
            </p>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <Metric label="Active Core ID" value={activeCoreStatus.activeCoreId} />
            <Metric label="Retained Core ID" value={activeCoreStatus.retainedCoreId} />
          </div>

          {activeCoreStatus.rollbackScheduled ? (
            <p className="font-mono text-caption text-foreground/45 leading-relaxed">
              Rollback is scheduled for the next animaOS restart. The current Core remains active
              until shutdown.
            </p>
          ) : (
            <>
              <label className="flex items-start gap-3 font-mono text-caption text-foreground/45 leading-relaxed">
                <input
                  type="checkbox"
                  checked={rollbackConfirmed}
                  onChange={(event) => setRollbackConfirmed(event.target.checked)}
                  className="mt-0.5"
                />
                I understand that the retained Core will become active only after restart, and the
                current Core will then be retained for recovery.
              </label>
              <div>
                <ActionButton
                  onClick={handleScheduleRollback}
                  disabled={rollbackBusy || !rollbackConfirmed}
                >
                  {rollbackBusy ? "Scheduling…" : "Rollback on restart"}
                </ActionButton>
              </div>
            </>
          )}

          {rollbackStatus && (
            <p className="font-mono text-caption text-foreground/45 leading-relaxed">
              {rollbackStatus}
            </p>
          )}
        </section>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-hairline px-3 py-2.5">
      <span className="block font-mono text-micro uppercase tracking-caps-2 text-foreground/25">{label}</span>
      <span className="mt-1 block font-mono text-caption text-foreground/55">{value}</span>
    </div>
  );
}

function ActionButton({
  onClick,
  disabled,
  children,
}: {
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="shrink-0 border border-hairline px-4 py-2 font-mono text-label uppercase tracking-caps-3 text-foreground/50 transition-colors hover:border-hairline-strong hover:text-foreground/75 disabled:opacity-30"
    >
      {children}
    </button>
  );
}
