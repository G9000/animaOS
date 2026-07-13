import { useState } from "react";
import { api, setUnlockToken } from "../../lib/api";
import {
  beginRecoveryPhraseReview,
  completeRecoveryPhraseReview,
  validateNewPassword,
  validateRecoveryPhraseConfirmation,
  type RecoveryPhraseReview,
} from "./recoveryCredential";

const glass = "bg-background/25 backdrop-blur-[40px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.22)]";
const INPUT_CLASS = "w-full bg-foreground/[0.04] border border-foreground/[0.08] px-3 py-2 text-sm text-foreground placeholder:text-foreground/25 outline-none focus:border-foreground/[0.18] transition-colors font-mono";

export default function SecuritySettings() {
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

  const handleReplaceRecovery = async (event: React.FormEvent) => {
    event.preventDefault();
    setRecoveryStatus("");
    setRecoveryError("");
    if (!currentRecoveryPhrase.trim() || !currentRecoveryPassword) {
      setRecoveryError("Current password and recovery phrase are required.");
      return;
    }
    setReplacingRecovery(true);
    try {
      const result = await api.auth.prepareRecoveryCredential(
        currentRecoveryPhrase,
        currentRecoveryPassword,
        "full",
      );
      setCurrentRecoveryPhrase("");
      setCurrentRecoveryPassword("");
      setRecoveryConfirmation("");
      setRecoveryReview(
        beginRecoveryPhraseReview(
          result.recoveryPhrase,
          result.pendingGeneration,
          result.scope,
          currentRecoveryPassword,
        ),
      );
    } catch (err) {
      setRecoveryError(
        err instanceof Error ? err.message : "Recovery credential replacement failed.",
      );
    } finally {
      setReplacingRecovery(false);
    }
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
      <h2 className="font-mono text-[9px] tracking-[0.22em] uppercase text-foreground/40">
        Master Password
      </h2>
      <p className="font-mono text-[10px] text-foreground/30 tracking-wide leading-relaxed">
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
            className="font-mono text-[9px] tracking-[0.18em] uppercase px-5 py-2.5 border border-accent/30 text-accent/70 hover:border-accent/60 hover:text-accent hover:bg-accent/[0.04] disabled:opacity-30 transition-all"
          >
            {changing ? "Updating..." : "Change Password"}
          </button>
          {changeStatus && <span className="font-mono text-[9px] text-accent/60 tracking-[0.18em] uppercase">{changeStatus}</span>}
          {changeError && <span className="font-mono text-[9px] text-destructive/70 tracking-wider">{changeError}</span>}
        </div>
      </form>

      <div className="h-px bg-foreground/[0.06]" />

      <section className="space-y-4">
        <h2 className="font-mono text-[9px] tracking-[0.22em] uppercase text-foreground/40">
          Recovery Credential
        </h2>
        <p className="font-mono text-[10px] text-foreground/30 tracking-wide leading-relaxed">
          Replaces the recovery wrappers for the Soul and every retained filesystem root. The new phrase is shown once.
        </p>

        {recoveryReview?.phase === "review" && recoveryReview.phrase ? (
          <div className="space-y-4 border border-accent/20 bg-accent/[0.03] p-4">
            <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-accent/70">
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
              className="font-mono text-[9px] tracking-[0.18em] uppercase px-5 py-2.5 border border-accent/30 text-accent/70 hover:border-accent/60 hover:text-accent transition-all"
            >
              Confirm Phrase
            </button>
            {recoveryReview.error && (
              <span className="block font-mono text-[9px] text-destructive/70 tracking-wider">
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
              className="font-mono text-[9px] tracking-[0.18em] uppercase px-5 py-2.5 border border-accent/30 text-accent/70 hover:border-accent/60 hover:text-accent hover:bg-accent/[0.04] disabled:opacity-30 transition-all"
            >
              {replacingRecovery ? "Replacing..." : "Replace Recovery Phrase"}
            </button>
          </form>
        )}
        {recoveryStatus && (
          <span className="font-mono text-[9px] text-accent/60 tracking-[0.18em] uppercase">
            {recoveryStatus}
          </span>
        )}
        {recoveryError && (
          <span className="font-mono text-[9px] text-destructive/70 tracking-wider">
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
      <h3 className="font-mono text-[9px] tracking-[0.18em] uppercase text-foreground/30">{label}</h3>
      {children}
    </div>
  );
}
