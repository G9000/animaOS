import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { api } from "../../lib/api";

const glass = "bg-background/25 backdrop-blur-[40px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.22)]";
const INPUT_CLASS =
  "w-full bg-foreground/[0.04] border border-foreground/[0.08] px-3 py-2 text-sm text-foreground placeholder:text-foreground/25 outline-none focus:border-foreground/[0.18] transition-colors font-mono";

export default function VaultSettings() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [vaultPassphrase, setVaultPassphrase] = useState("");
  const [vaultPayload, setVaultPayload] = useState("");
  const [vaultBusy, setVaultBusy] = useState(false);
  const [vaultStatus, setVaultStatus] = useState("");

  const handleVaultExport = async () => {
    if (!vaultPassphrase || vaultPassphrase.length < 8) { setVaultStatus("Passphrase must be at least 8 characters."); return; }
    setVaultBusy(true); setVaultStatus("");
    try {
      const result = await api.vault.export(vaultPassphrase);
      const blob = new Blob([result.vault], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = result.filename; anchor.click();
      URL.revokeObjectURL(url);
      setVaultStatus(`Vault exported (${Math.round(result.size / 1024)} KB).`);
    } catch (err) {
      setVaultStatus(err instanceof Error ? err.message : "Vault export failed.");
    } finally { setVaultBusy(false); }
  };

  const handleVaultImport = async () => {
    if (!vaultPassphrase || vaultPassphrase.length < 8) { setVaultStatus("Passphrase must be at least 8 characters."); return; }
    if (!vaultPayload.trim()) { setVaultStatus("Paste vault payload or load a vault file first."); return; }
    setVaultBusy(true); setVaultStatus("");
    try {
      const result = await api.vault.import(vaultPassphrase, vaultPayload);
      if (result.requiresReauth) { await logout(); navigate("/login", { replace: true }); return; }
      setVaultStatus(`Vault restored: ${result.restoredUsers} users, ${result.restoredMemoryFiles} memory files.`);
    } catch (err) {
      setVaultStatus(err instanceof Error ? err.message : "Vault import failed.");
    } finally { setVaultBusy(false); }
  };

  const handleVaultFile = async (file: File | null) => {
    if (!file) return;
    setVaultPayload(await file.text());
    setVaultStatus(`Loaded ${file.name}.`);
  };

  return (
    <div className={`${glass} p-6 space-y-5`}>
      <h2 className="font-mono text-[9px] tracking-[0.22em] uppercase text-foreground/40">
        Vault Backup
      </h2>

      <p className="font-mono text-[10px] text-foreground/30 tracking-wide leading-relaxed">
        Export or restore the encrypted vault bundle independently from runtime AI configuration.
      </p>

      <div className="h-px bg-foreground/[0.06]" />

      <div className="space-y-1.5">
        <h3 className="font-mono text-[9px] tracking-[0.18em] uppercase text-foreground/30">Vault Passphrase</h3>
        <input
          type="password"
          value={vaultPassphrase}
          onChange={(e) => setVaultPassphrase(e.target.value)}
          className={INPUT_CLASS}
          placeholder="Vault passphrase (min 8 chars)"
        />
      </div>

      <div className="flex flex-wrap gap-2">
        <ActionButton onClick={handleVaultExport} disabled={vaultBusy}>
          {vaultBusy ? "Working..." : "Export Vault"}
        </ActionButton>
        <ActionButton onClick={handleVaultImport} disabled={vaultBusy}>
          {vaultBusy ? "Working..." : "Import Vault"}
        </ActionButton>
        <label className="font-mono text-[9px] tracking-[0.18em] uppercase px-4 py-2.5 border border-foreground/[0.1] text-foreground/40 hover:border-foreground/[0.2] hover:text-foreground/70 transition-all cursor-pointer">
          Load File
          <input type="file" accept="application/json,.json,.vault" className="hidden" onChange={(e) => { void handleVaultFile(e.target.files?.[0] || null); }} />
        </label>
      </div>

      <div className="h-px bg-foreground/[0.06]" />

      <div className="space-y-1.5">
        <h3 className="font-mono text-[9px] tracking-[0.18em] uppercase text-foreground/30">Vault Payload</h3>
        <textarea
          value={vaultPayload}
          onChange={(e) => setVaultPayload(e.target.value)}
          rows={8}
          className={`${INPUT_CLASS} resize-y leading-relaxed text-xs`}
          placeholder="Vault JSON payload (for import)..."
        />
      </div>

      {vaultStatus && (
        <p className="font-mono text-[10px] text-foreground/40 tracking-wide">{vaultStatus}</p>
      )}
    </div>
  );
}

function ActionButton({ onClick, disabled, children }: { onClick: () => void; disabled?: boolean; children: React.ReactNode }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="font-mono text-[9px] tracking-[0.18em] uppercase px-4 py-2.5 border border-foreground/[0.1] text-foreground/40 hover:border-foreground/[0.2] hover:text-foreground/70 disabled:opacity-30 transition-all"
    >
      {children}
    </button>
  );
}
