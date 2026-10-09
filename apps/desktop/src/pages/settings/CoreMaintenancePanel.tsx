import { useState } from "react";
import type { CoreMaintenanceInventory, CoreMaintenanceParameters, CoreMaintenanceReview } from "@anima/api-client";
import { api, setUnlockToken } from "../../lib/api";
import { canConfirmMaintenance, maintenanceProgress } from "./maintenanceReview";

const INPUT_CLASS = "w-full bg-foreground/[0.04] border border-hairline px-3 py-2 text-sm text-foreground outline-none focus:border-hairline-strong";
const BUTTON_CLASS = "border border-hairline px-4 py-2 text-sm text-foreground disabled:opacity-40";

export default function CoreMaintenancePanel({ onChanged }: { onChanged: () => Promise<unknown> }) {
  const [password, setPassword] = useState("");
  const [recovery, setRecovery] = useState("");
  const [backupPath, setBackupPath] = useState("");
  const [backupPassword, setBackupPassword] = useState("");
  const [action, setAction] = useState<CoreMaintenanceParameters["action"]>("prune");
  const [target, setTarget] = useState("");
  const [revision, setRevision] = useState("");
  const [oldKey, setOldKey] = useState("");
  const [waiveHistory, setWaiveHistory] = useState(false);
  const [acknowledgeCopies, setAcknowledgeCopies] = useState(false);
  const [inventory, setInventory] = useState<CoreMaintenanceInventory | null>(null);
  const [review, setReview] = useState<CoreMaintenanceReview | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const resetReview = () => { setReview(null); setConfirmed(false); setError(""); setMessage(""); };
  const parameters = (): CoreMaintenanceParameters => ({
    action,
    ...(action === "purge" ? { targetStableId: target.trim(), expectedRevision: Number(revision), waiveRestoreHistory: waiveHistory, acknowledgeBackupCopies: acknowledgeCopies } : {}),
    ...(action === "retire-key" ? { retiringFrkVersion: Number(oldKey) } : {}),
  });
  const request = () => ({ currentPassword: password, recoveryPhrase: recovery, backupPath, backupPassphrase: backupPassword, parameters: parameters() });
  const failure = (reason: unknown) => setError(reason instanceof Error ? reason.message : "Core maintenance could not finish.");

  const inspect = async () => {
    resetReview(); setBusy(true);
    try { setInventory(await api.corefs.maintenanceInventory({ currentPassword: password, recoveryPhrase: recovery })); }
    catch (reason) { failure(reason); }
    finally { setBusy(false); }
  };
  const prepare = async () => {
    resetReview(); setBusy(true);
    try {
      const prepared = await api.corefs.prepareMaintenance(request());
      setReview(prepared); setInventory(prepared.inventory);
    } catch (reason) { failure(reason); }
    finally { setBusy(false); }
  };
  const execute = async () => {
    if (!canConfirmMaintenance(review, confirmed, Date.now()) || !review) {
      setError("Inspect and prepare again; the confirmation may have expired."); return;
    }
    setBusy(true); setError("");
    const prepared = review;
    setReview(null); setConfirmed(false);
    try {
      const result = await api.corefs.executeMaintenance({ ...request(), confirmation: prepared.confirmation, inventoryDigest: prepared.inventory.inventoryDigest, observedAtMs: prepared.observedAtMs });
      if (result.unlockToken) setUnlockToken(result.unlockToken);
      setInventory(result.inventory);
      setMessage(result.inventory.operation && !["complete", "completed", "cancelled"].includes(result.inventory.operation.state)
        ? "The cleanup batch finished. Review progress, then prepare a resume to continue."
        : "Core maintenance finished. Your backup remains available.");
      setPassword(""); setRecovery(""); setBackupPassword("");
      await onChanged();
    } catch (reason) { failure(reason); }
    finally { setBusy(false); }
  };

  return (
    <section className="space-y-5 border border-hairline p-5" aria-labelledby="core-maintenance-title">
      <div>
        <h2 id="core-maintenance-title" className="text-lg text-foreground">Core storage maintenance</h2>
        <p className="mt-2 text-sm text-foreground/60">Keep 30 days of restore history. Cleanup protects current files and recoverable Trash; permanently purging a deleted item is a separate decision.</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="space-y-1 text-sm">Current master password<input type="password" autoComplete="current-password" className={INPUT_CLASS} value={password} disabled={busy} onChange={event => { resetReview(); setPassword(event.target.value); }} /></label>
        <label className="space-y-1 text-sm">Current recovery phrase<input type="password" autoComplete="off" className={INPUT_CLASS} value={recovery} disabled={busy} onChange={event => { resetReview(); setRecovery(event.target.value); }} /></label>
      </div>
      <button type="button" className={BUTTON_CLASS} disabled={busy || !password || !recovery} onClick={() => void inspect()}>Inspect storage</button>
      {inventory && (
        <div className="space-y-2 border border-hairline p-4 text-sm" aria-live="polite">
          <p>{inventory.retainedCatalogCount} versions protected. Up to {(inventory.candidateBytes / (1024 * 1024)).toFixed(2)} MB reclaimable.</p>
          <p>{inventory.candidateCatalogCount} old versions, {inventory.candidateObjectCount} unused objects, and {inventory.candidatePreparationCount} completed preparation records eligible for cleanup.</p>
          {inventory.retainedUnknownAgeCount > 0 && <p>Versions with an unverified age stay protected until their restore window can be proven.</p>}
          <p>{maintenanceProgress(inventory.operation)}</p>
          {inventory.blockers.length > 0 && <p className="text-destructive">Blocked: {inventory.blockers.join(", ").replace(/_/g, " ")}</p>}
        </div>
      )}
      <div className="grid gap-4 md:grid-cols-2">
        <label className="space-y-1 text-sm">Full backup file<input className={INPUT_CLASS} value={backupPath} placeholder="Absolute path to your .anima backup" disabled={busy} onChange={event => { resetReview(); setBackupPath(event.target.value); }} /></label>
        <label className="space-y-1 text-sm">Backup passphrase<input type="password" autoComplete="off" className={INPUT_CLASS} value={backupPassword} disabled={busy} onChange={event => { resetReview(); setBackupPassword(event.target.value); }} /></label>
      </div>
      <p className="text-sm text-foreground/60">Use Export ANIMA CORE to create a full backup of the current version. It will be authenticated and checked again before maintenance.</p>
      <label className="block space-y-1 text-sm">Operation
        <select className={INPUT_CLASS} value={action} disabled={busy} onChange={event => { resetReview(); setAction(event.target.value as CoreMaintenanceParameters["action"]); }}>
          <option value="prune">Clean up old versions</option>
          <option value="resume">Resume interrupted cleanup</option>
          <option value="cancel">Cancel before deletion</option>
          <option value="purge">Permanently purge a deleted item</option>
          <option value="retire-key">Retire a previous encryption key</option>
          <option value="refresh-checkpoint">Update recovery checkpoint to the current version</option>
        </select>
      </label>
      {action === "purge" && (
        <div className="space-y-3 border border-destructive/30 p-4 text-sm">
          <label className="block">Deleted item identifier<input className={INPUT_CLASS} value={target} disabled={busy} onChange={event => { resetReview(); setTarget(event.target.value); }} /></label>
          <label className="block">Exact deleted revision<input type="number" min="1" className={INPUT_CLASS} value={revision} disabled={busy} onChange={event => { resetReview(); setRevision(event.target.value); }} /></label>
          <label className="flex gap-2"><input type="checkbox" checked={waiveHistory} disabled={busy} onChange={event => { resetReview(); setWaiveHistory(event.target.checked); }} />Remove the confirmed item and its local restore history.</label>
          <label className="flex gap-2"><input type="checkbox" checked={acknowledgeCopies} disabled={busy} onChange={event => { resetReview(); setAcknowledgeCopies(event.target.checked); }} />I understand that exports, backups and storage remnants may retain copies.</label>
        </div>
      )}
      {action === "retire-key" && <label className="block text-sm">Previous key version<input type="number" min="1" className={INPUT_CLASS} value={oldKey} disabled={busy} onChange={event => { resetReview(); setOldKey(event.target.value); }} /></label>}
      <button type="button" className={BUTTON_CLASS} disabled={busy || !password || !recovery || !backupPath || !backupPassword} onClick={() => void prepare()}>Verify backup and prepare review</button>
      {review && (
        <div className="space-y-3 border border-destructive/30 p-4 text-sm" role="region" aria-label="Maintenance confirmation">
          <p>Backup verified. This confirmation lasts two minutes and applies only to this exact inventory.</p>
          {review.parameters.action === "purge" && <p>{review.inventory.targetObjectCount ?? 0} deleted objects selected; {review.inventory.waivedGenerations?.length ?? 0} history versions require removal.</p>}
          <p>{review.inventory.deletionLimits}</p>
          <label className="flex gap-2"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} disabled={busy} />I have reviewed this operation and approve its irreversible changes.</label>
          <button type="button" className={`${BUTTON_CLASS} text-destructive`} disabled={busy || !canConfirmMaintenance(review, confirmed, Date.now())} onClick={() => void execute()}>Confirm maintenance</button>
        </div>
      )}
      {message && <p role="status" className="text-sm text-accent">{message}</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </section>
  );
}
