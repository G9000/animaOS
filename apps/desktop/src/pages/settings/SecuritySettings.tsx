import { useState } from "react";
import { api, setUnlockToken } from "../../lib/api";
import { useCoreFSReadiness } from "../../context/CoreFSReadinessContext";
import { glass } from "@anima/standard-templates";
import {
  beginRecoveryPhraseReview,
  completeRecoveryPhraseReview,
  validateNewPassword,
  validateRecoveryPhraseConfirmation,
  type RecoveryPhraseReview,
} from "./recoveryCredential";

const INPUT_CLASS = "w-full bg-foreground/[0.04] border border-hairline px-3 py-2 text-sm text-foreground placeholder:text-foreground/25 outline-none focus:border-hairline-strong transition-colors font-mono";

export default function SecuritySettings() {
  const {
    status: coreFSStatus,
    loading: coreFSLoading,
    error: coreFSError,
    refresh: refreshCoreFS,
  } = useCoreFSReadiness();
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changing, setChanging] = useState(false);
  const [changeStatus, setChangeStatus] = useState("");
  const [changeError, setChangeError] = useState("");
  const [currentRecoveryPhrase, setCurrentRecoveryPhrase] = useState("");
  const [currentRecoveryPassword, setCurrentRecoveryPassword] = useState("");
  const [replacingRecovery, setReplacingRecovery] = useState(false);
  const [recoveryReview, setRecoveryReview] = useState<RecoveryPhraseReview | null>(null);
  const [recoveryConfirmation, setRecoveryConfirmation] = useState("");
  const [recoveryStatus, setRecoveryStatus] = useState("");
  const [recoveryError, setRecoveryError] = useState("");
  const [canReplacePendingRecovery, setCanReplacePendingRecovery] = useState(false);
  const [rotationPassword, setRotationPassword] = useState("");
  const [rotationRecoveryPhrase, setRotationRecoveryPhrase] = useState("");
  const [rotating, setRotating] = useState(false);
  const [rotationStatus, setRotationStatus] = useState("");
  const [rotationError, setRotationError] = useState("");

  const handleChangePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setChangeStatus("");
    setChangeError("");
    const passwordError = validateNewPassword(newPassword);
    if (passwordError) { setChangeError(passwordError); return; }
    if (newPassword !== confirmPassword) { setChangeError("New password confirmation does not match."); return; }
    setChanging(true);
    try {
      const result = await api.auth.changePassword(oldPassword, newPassword);
      setUnlockToken(result.unlockToken);
      setOldPassword(""); setNewPassword(""); setConfirmPassword("");
      setChangeStatus("Master password updated. Unlock session rotated.");
    } catch (err) {
      setChangeError(err instanceof Error ? err.message : "Password change failed.");
    } finally {
      setChanging(false);
    }
  };

  const handleRotateFilesystemKey = async (event: React.FormEvent) => {
    event.preventDefault();
    setRotationStatus("");
    setRotationError("");
    if (!rotationPassword || !rotationRecoveryPhrase.trim()) {
      setRotationError("Current password and recovery phrase are required.");
      return;
    }
    setRotating(true);
    try {
      const result = await api.corefs.rotateRootKey(
        rotationPassword,
        rotationRecoveryPhrase,
      );
      setUnlockToken(result.unlockToken);
      setRotationPassword("");
      setRotationRecoveryPhrase("");
      setRotationStatus(
        `${result.resumed ? "Resumed" : "Completed"} FRK v${result.activeFrkVersion} at catalog generation ${result.committedCatalogGeneration}.`,
      );
      await refreshCoreFS();
    } catch (reason) {
      setRotationError(
        reason instanceof Error
          ? reason.message
          : "Filesystem key rotation failed.",
      );
    } finally {
      setRotating(false);
    }
  };

  const prepareRecoveryReplacement = async (replacePending: boolean) => {
    setRecoveryStatus("");
    setRecoveryError("");
    setCanReplacePendingRecovery(false);
    if (!currentRecoveryPhrase.trim() || !currentRecoveryPassword) {
      setRecoveryError("Current password and recovery phrase are required.");
      return;
    }
    const confirmationPassword = currentRecoveryPassword;
    setReplacingRecovery(true);
    try {
      const result = await api.auth.prepareRecoveryCredential(
        currentRecoveryPhrase,
        currentRecoveryPassword,
        "full",
        replacePending,
      );
      setCurrentRecoveryPhrase("");
      setCurrentRecoveryPassword("");
      setRecoveryConfirmation("");
      setRecoveryReview(
        beginRecoveryPhraseReview(
          result.recoveryPhrase,
          result.pendingGeneration,
          result.scope,
          confirmationPassword,
        ),
      );
    } catch (err) {
      const message = err instanceof Error
        ? err.message
        : "Recovery credential replacement failed.";
      setRecoveryError(message);
      setCanReplacePendingRecovery(
        message.includes("recovery credential preparation is in progress"),
      );
    } finally {
      setReplacingRecovery(false);
    }
  };

  const handleReplaceRecovery = async (event: React.FormEvent) => {
    event.preventDefault();
    await prepareRecoveryReplacement(false);
  };

  const handleConfirmRecovery = async () => {
    if (!recoveryReview) return;
    const checked = validateRecoveryPhraseConfirmation(
      recoveryReview,
      recoveryConfirmation,
    );
    setRecoveryReview(checked);
    if (
      checked.error
      || checked.phrase === null
      || checked.pendingGeneration === null
      || checked.scope === null
      || checked.currentPassword === null
    ) {
      return;
    }
    setReplacingRecovery(true);
    setRecoveryError("");
    try {
      await api.auth.confirmRecoveryCredential(
        checked.phrase,
        checked.pendingGeneration,
        checked.scope,
        checked.currentPassword,
      );
      setRecoveryReview(completeRecoveryPhraseReview(checked));
      setRecoveryConfirmation("");
      setRecoveryStatus("Recovery credential replaced and confirmed.");
    } catch (err) {
      setRecoveryError(
        err instanceof Error ? err.message : "Recovery credential confirmation failed.",
      );
    } finally {
      setReplacingRecovery(false);
    }
  };

  return (
    <div className={`${glass} p-6 space-y-5`}>
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-4">
          <h2 className="font-mono text-label tracking-caps-4 uppercase text-foreground/40">
            Portable Core
          </h2>
          <button
            type="button"
            onClick={() => void refreshCoreFS()}
            disabled={coreFSLoading}
            className="font-mono text-micro tracking-caps-3 uppercase text-foreground/35 hover:text-foreground/70 disabled:opacity-30 transition-colors"
          >
            {coreFSLoading ? "Checking..." : "Refresh"}
          </button>
        </div>
        {coreFSStatus ? (
          <div className="grid grid-cols-2 gap-3 font-mono text-label tracking-wide">
            <StatusCell
              label="Index"
              value={coreFSStatus.readiness.state.replace(/_/g, " ")}
            />
            <StatusCell
              label="Catalog"
              value={
                coreFSStatus.readiness.catalogGeneration === null
                  ? "waiting"
                  : `generation ${coreFSStatus.readiness.catalogGeneration}`
              }
            />
            <StatusCell
              label="Filesystem Key"
              value={`FRK v${coreFSStatus.rotation.activeFrkVersion}`}
            />
            <StatusCell
              label="Blind Index"
              value={
                coreFSStatus.rotation.blindIndexGeneration === null
                  ? "rebuilding"
                  : `generation ${coreFSStatus.rotation.blindIndexGeneration}`
              }
            />
            <StatusCell
              label="Password Reopen"
              value={
                coreFSStatus.rotation.passwordReopenVerified
                  ? "verified"
                  : "not verified"
              }
            />
            <StatusCell
              label="Recovery Reopen"
              value={
                coreFSStatus.rotation.recoveryReopenVerified
                  ? "verified"
                  : "not verified"
              }
            />
          </div>
        ) : (
          <p className="font-mono text-label text-foreground/30 tracking-wide">
            {coreFSError ?? "CoreFS status is not available while locked."}
          </p>
        )}
      </section>

      <div className="h-px bg-foreground/[0.06]" />

      <section className="space-y-4">
        <h2 className="font-mono text-label tracking-caps-4 uppercase text-foreground/40">
          Filesystem Root Key
        </h2>
        <p className="font-mono text-caption text-foreground/30 tracking-wide leading-relaxed">
          Rewraps the committed Core catalog under a new filesystem root. Both
          credential paths are verified before the new generation is activated.
        </p>
        {coreFSStatus && (
          <div className="border border-hairline-faint bg-foreground/[0.02] p-3">
            <p className="font-mono text-label uppercase tracking-caps-3 text-foreground/45">
              Old-key retirement:{" "}
              {coreFSStatus.rotation.oldKeyRetirementSafe ? "safe" : "blocked"}
            </p>
            {!coreFSStatus.rotation.oldKeyRetirementSafe && (
              <p className="mt-1 font-mono text-micro tracking-wide text-foreground/30">
                {coreFSStatus.rotation.oldKeyRetirementBlockers
                  .map((blocker) => blocker.replace(/_/g, " "))
                  .join(" · ")}
              </p>
            )}
          </div>
        )}
        <form onSubmit={handleRotateFilesystemKey} className="space-y-4">
          <Field label="Current Password">
            <input
              type="password"
              value={rotationPassword}
              onChange={(event) => setRotationPassword(event.target.value)}
              className={INPUT_CLASS}
              autoComplete="current-password"
            />
          </Field>
          <Field label="Recovery Phrase">
            <input
              type="password"
              value={rotationRecoveryPhrase}
              onChange={(event) =>
                setRotationRecoveryPhrase(event.target.value)
              }
              className={INPUT_CLASS}
              autoComplete="off"
            />
          </Field>
          <button
            type="submit"
            disabled={rotating}
            className="font-mono text-label tracking-caps-3 uppercase px-5 py-2.5 border border-accent/30 text-accent/70 hover:border-accent/60 hover:text-accent hover:bg-accent/[0.04] disabled:opacity-30 transition-all"
          >
            {rotating ? "Rotating..." : "Rotate Filesystem Key"}
          </button>
        </form>
        {rotationStatus && (
          <span className="block font-mono text-label text-accent/60 tracking-wide">
            {rotationStatus}
          </span>
        )}
        {rotationError && (
          <span className="block font-mono text-label text-destructive/70 tracking-wide">
            {rotationError}
          </span>
        )}
      </section>

      <div className="h-px bg-foreground/[0.06]" />

      <h2 className="font-mono text-label tracking-caps-4 uppercase text-foreground/40">
        Master Password
      </h2>
      <p className="font-mono text-caption text-foreground/30 tracking-wide leading-relaxed">
        This password rewraps the vault DEK and controls future unlock sessions.
      </p>

      <div className="h-px bg-foreground/[0.06]" />

      <form onSubmit={handleChangePassword} className="space-y-4">
        <Field label="Current Password">
          <input type="password" value={oldPassword} onChange={(e) => setOldPassword(e.target.value)} className={INPUT_CLASS} autoComplete="current-password" />
        </Field>
        <Field label="New Password">
          <input type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} className={INPUT_CLASS} autoComplete="new-password" />
        </Field>
        <Field label="Confirm New Password">
          <input type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} className={INPUT_CLASS} autoComplete="new-password" />
        </Field>

        <div className="h-px bg-foreground/[0.06]" />

        <div className="flex items-center gap-4">
          <button
            type="submit"
            disabled={changing}
            className="font-mono text-label tracking-caps-3 uppercase px-5 py-2.5 border border-accent/30 text-accent/70 hover:border-accent/60 hover:text-accent hover:bg-accent/[0.04] disabled:opacity-30 transition-all"
          >
            {changing ? "Updating..." : "Change Password"}
          </button>
          {changeStatus && <span className="font-mono text-label text-accent/60 tracking-caps-3 uppercase">{changeStatus}</span>}
          {changeError && <span className="font-mono text-label text-destructive/70 tracking-wider">{changeError}</span>}
        </div>
      </form>

      <div className="h-px bg-foreground/[0.06]" />

      <section className="space-y-4">
        <h2 className="font-mono text-label tracking-caps-4 uppercase text-foreground/40">
          Recovery Credential
        </h2>
        <p className="font-mono text-caption text-foreground/30 tracking-wide leading-relaxed">
          Replaces the recovery wrappers for the Soul and every retained filesystem root. The new phrase is shown once.
        </p>

        {recoveryReview?.phase === "review" && recoveryReview.phrase ? (
          <div className="space-y-4 border border-accent/20 bg-accent/[0.03] p-4">
            <p className="font-mono text-label uppercase tracking-caps-3 text-accent/70">
              Record this new phrase now
            </p>
            <pre className="whitespace-pre-wrap break-words font-mono text-sm text-foreground" data-testid="new-recovery-phrase">
              {recoveryReview.phrase}
            </pre>
            <Field label="Type the new phrase to confirm">
              <input
                type="text"
                value={recoveryConfirmation}
                onChange={(event) => setRecoveryConfirmation(event.target.value)}
                className={INPUT_CLASS}
                autoComplete="off"
              />
            </Field>
            <button
              type="button"
              onClick={handleConfirmRecovery}
              className="font-mono text-label tracking-caps-3 uppercase px-5 py-2.5 border border-accent/30 text-accent/70 hover:border-accent/60 hover:text-accent transition-all"
            >
              Confirm Phrase
            </button>
            {recoveryReview.error && (
              <span className="block font-mono text-label text-destructive/70 tracking-wider">
                {recoveryReview.error}
              </span>
            )}
          </div>
        ) : (
          <form onSubmit={handleReplaceRecovery} className="space-y-4">
            <Field label="Current Recovery Phrase">
              <input
                type="password"
                value={currentRecoveryPhrase}
                onChange={(event) => setCurrentRecoveryPhrase(event.target.value)}
                className={INPUT_CLASS}
                autoComplete="off"
              />
            </Field>
            <Field label="Current Password">
              <input
                type="password"
                value={currentRecoveryPassword}
                onChange={(event) => setCurrentRecoveryPassword(event.target.value)}
                className={INPUT_CLASS}
                autoComplete="current-password"
              />
            </Field>
            <button
              type="submit"
              disabled={replacingRecovery}
              className="font-mono text-label tracking-caps-3 uppercase px-5 py-2.5 border border-accent/30 text-accent/70 hover:border-accent/60 hover:text-accent hover:bg-accent/[0.04] disabled:opacity-30 transition-all"
            >
              {replacingRecovery ? "Replacing..." : "Replace Recovery Phrase"}
            </button>
            {canReplacePendingRecovery && (
              <button
                type="button"
                disabled={replacingRecovery}
                onClick={() => void prepareRecoveryReplacement(true)}
                className="font-mono text-label tracking-caps-3 uppercase px-5 py-2.5 border border-destructive/30 text-destructive/70 hover:border-destructive/60 hover:text-destructive disabled:opacity-30 transition-all"
              >
                Discard Pending Phrase and Generate Again
              </button>
            )}
          </form>
        )}
        {recoveryStatus && (
          <span className="font-mono text-label text-accent/60 tracking-caps-3 uppercase">
            {recoveryStatus}
          </span>
        )}
        {recoveryError && (
          <span className="font-mono text-label text-destructive/70 tracking-wider">
            {recoveryError}
          </span>
        )}
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <h3 className="font-mono text-label tracking-caps-3 uppercase text-foreground/30">{label}</h3>
      {children}
    </div>
  );
}

function StatusCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-hairline-faint bg-foreground/[0.02] px-3 py-2.5">
      <p className="text-micro uppercase tracking-caps-3 text-foreground/25">
        {label}
      </p>
      <p className="mt-1 capitalize text-foreground/60">{value}</p>
    </div>
  );
}
