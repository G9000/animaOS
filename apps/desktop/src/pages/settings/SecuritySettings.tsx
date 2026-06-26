import { useState } from "react";
import { api, setUnlockToken } from "../../lib/api";

const glass = "bg-background/25 backdrop-blur-[40px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.22)]";
const INPUT_CLASS = "w-full bg-foreground/[0.04] border border-foreground/[0.08] px-3 py-2 text-sm text-foreground placeholder:text-foreground/25 outline-none focus:border-foreground/[0.18] transition-colors font-mono";

export default function SecuritySettings() {
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [changing, setChanging] = useState(false);
  const [changeStatus, setChangeStatus] = useState("");
  const [changeError, setChangeError] = useState("");

  const handleChangePassword = async (event: React.FormEvent) => {
    event.preventDefault();
    setChangeStatus("");
    setChangeError("");
    if (newPassword.length < 6) { setChangeError("New password must be at least 6 characters."); return; }
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
