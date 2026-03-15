import { useState } from "react";
import { api, getUnlockToken, setUnlockToken } from "../../lib/api";

const INPUT_CLASS =
  "w-full bg-bg-input border border-border rounded-sm px-3 py-2 text-sm text-text placeholder:text-text-muted/50 outline-none focus:border-primary transition-colors";

export default function SecuritySettings() {
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changing, setChanging] = useState(false);
  const [changeStatus, setChangeStatus] = useState("");
  const [changeError, setChangeError] = useState("");
  const [showUnlockKey, setShowUnlockKey] = useState(false);
  const [unlockKey, setUnlockKey] = useState("");
  const [unlockCopied, setUnlockCopied] = useState(false);

  const handleCopyUnlockKey = async () => {
    if (!unlockKey) return;
    try {
      await navigator.clipboard.writeText(unlockKey);
      setUnlockCopied(true);
      setTimeout(() => setUnlockCopied(false), 1500);
    } catch {
      // Ignore clipboard failures.
    }
  };

  const handleChangePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setChangeStatus("");
    setChangeError("");

    if (newPassword.length < 6) {
      setChangeError("New password must be at least 6 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setChangeError("New password confirmation does not match.");
      return;
    }

    setChanging(true);
    try {
      const result = await api.auth.changePassword(oldPassword, newPassword);
      setUnlockToken(result.unlockToken);
      setUnlockKey(result.unlockToken);
      setOldPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setChangeStatus("Master password updated. Unlock session rotated.");
    } catch (err) {
      setChangeError(err instanceof Error ? err.message : "Password change failed.");
    } finally {
      setChanging(false);
    }
  };

  const revealUnlockKey = () => {
    setUnlockKey(getUnlockToken() || "");
    setShowUnlockKey(true);
  };

  return (
    <div className="space-y-6">
      <section className="rounded-sm border border-border bg-bg-card p-5 space-y-5">
        <header className="space-y-1">
          <h2 className="text-[11px] text-text-muted uppercase tracking-wider">
            Master Password
          </h2>
          <p className="text-xs text-text-muted">
            This password rewraps the vault DEK and controls future unlock sessions.
          </p>
        </header>

        <form onSubmit={handleChangePassword} className="space-y-4">
          <Field label="Current Password">
            <input
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              className={INPUT_CLASS}
              autoComplete="current-password"
            />
          </Field>
          <Field label="New Password">
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              className={INPUT_CLASS}
              autoComplete="new-password"
            />
          </Field>
          <Field label="Confirm New Password">
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className={INPUT_CLASS}
              autoComplete="new-password"
            />
          </Field>

          <div className="flex items-center gap-4">
            <button
              type="submit"
              disabled={changing}
              className="px-5 py-2 bg-bg-input border border-primary text-text text-xs uppercase tracking-wider rounded-sm hover:bg-bg disabled:opacity-50 transition-colors"
            >
              {changing ? "Updating..." : "Change Password"}
            </button>
            {changeStatus && (
              <span className="text-xs text-primary tracking-wider">
                {changeStatus}
              </span>
            )}
            {changeError && (
              <span className="text-xs text-danger tracking-wider">
                {changeError}
              </span>
            )}
          </div>
        </form>
      </section>

      <section className="rounded-sm border border-border bg-bg-card p-5 space-y-4">
        <header className="space-y-1">
          <h2 className="text-[11px] text-text-muted uppercase tracking-wider">
            Session Unlock Key
          </h2>
          <p className="text-xs text-text-muted">
            Hidden by default. This token unlocks decrypted local data for the current
            session only.
          </p>
        </header>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => {
              if (showUnlockKey) {
                setShowUnlockKey(false);
                return;
              }
              revealUnlockKey();
            }}
            className="px-4 py-2 border border-border rounded-sm text-xs uppercase tracking-wider hover:border-primary"
          >
            {showUnlockKey ? "Hide Key" : "Reveal Key"}
          </button>
          <button
            onClick={() => setUnlockKey(getUnlockToken() || "")}
            className="px-4 py-2 border border-border rounded-sm text-xs uppercase tracking-wider hover:border-primary"
          >
            Refresh
          </button>
          {showUnlockKey && (
            <button
              onClick={() => void handleCopyUnlockKey()}
              disabled={!unlockKey}
              className="px-4 py-2 border border-border rounded-sm text-xs uppercase tracking-wider hover:border-primary disabled:opacity-50"
            >
              {unlockCopied ? "Copied" : "Copy"}
            </button>
          )}
        </div>

        {showUnlockKey && (
          <textarea
            readOnly
            value={unlockKey || "No active unlock key. Sign in again to create one."}
            rows={3}
            className="w-full bg-bg-input border border-border rounded-sm px-3 py-2 text-xs text-text-muted outline-none resize-none"
          />
        )}
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-[11px] text-text-muted uppercase tracking-wider">
        {label}
      </h3>
      {children}
    </section>
  );
}
