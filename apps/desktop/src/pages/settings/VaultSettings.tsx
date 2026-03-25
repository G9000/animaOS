import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { api } from "../../lib/api";

const INPUT_CLASS =
  "w-full bg-input border border-border rounded-sm px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/50 outline-none focus:border-primary transition-colors";

export default function VaultSettings() {
  const { logout } = useAuth();
  const navigate = useNavigate();
  const [vaultPassphrase, setVaultPassphrase] = useState("");
  const [vaultPayload, setVaultPayload] = useState("");
  const [vaultBusy, setVaultBusy] = useState(false);
  const [vaultStatus, setVaultStatus] = useState("");

  const handleVaultExport = async () => {
    if (!vaultPassphrase || vaultPassphrase.length < 8) {
      setVaultStatus("Passphrase must be at least 8 characters.");
      return;
    }

    setVaultBusy(true);
    setVaultStatus("");
    try {
      const result = await api.vault.export(vaultPassphrase);
      const blob = new Blob([result.vault], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = result.filename;
      anchor.click();
      URL.revokeObjectURL(url);
      setVaultStatus(`Vault exported (${Math.round(result.size / 1024)} KB).`);
    } catch (err) {
      setVaultStatus(err instanceof Error ? err.message : "Vault export failed.");
    } finally {
      setVaultBusy(false);
    }
  };

  const handleVaultImport = async () => {
    if (!vaultPassphrase || vaultPassphrase.length < 8) {
      setVaultStatus("Passphrase must be at least 8 characters.");
      return;
    }
    if (!vaultPayload.trim()) {
      setVaultStatus("Paste vault payload or load a vault file first.");
      return;
    }

    setVaultBusy(true);
    setVaultStatus("");
    try {
      const result = await api.vault.import(vaultPassphrase, vaultPayload);
      if (result.requiresReauth) {
        await logout();
        navigate("/login", { replace: true });
        return;
      }
      setVaultStatus(
        `Vault restored: ${result.restoredUsers} users, ${result.restoredMemoryFiles} memory files.`,
      );
    } catch (err) {
      setVaultStatus(err instanceof Error ? err.message : "Vault import failed.");
    } finally {
      setVaultBusy(false);
    }
  };

  const handleVaultFile = async (file: File | null) => {
    if (!file) return;
    const text = await file.text();
    setVaultPayload(text);
    setVaultStatus(`Loaded ${file.name}.`);
  };

  return (
    <div className="space-y-6">
      <section className="rounded-sm border border-border bg-card p-5 space-y-5">
        <header className="space-y-1">
          <h2 className="text-[11px] text-muted-foreground uppercase tracking-wider">
            Vault Backup
          </h2>
          <p className="text-xs text-muted-foreground">
            Export or restore the encrypted vault bundle independently from runtime AI
            configuration.
          </p>
        </header>

        <section className="space-y-2">
          <h3 className="text-[11px] text-muted-foreground uppercase tracking-wider">
            Vault Passphrase
          </h3>
          <input
            type="password"
            value={vaultPassphrase}
            onChange={(e) => setVaultPassphrase(e.target.value)}
            className={INPUT_CLASS}
            placeholder="Vault passphrase (min 8 chars)"
          />
        </section>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={handleVaultExport}
            disabled={vaultBusy}
            className="px-4 py-2 border border-border rounded-sm text-xs uppercase tracking-wider hover:border-primary disabled:opacity-50"
          >
            {vaultBusy ? "Working..." : "Export Vault"}
          </button>
          <button
            onClick={handleVaultImport}
            disabled={vaultBusy}
            className="px-4 py-2 border border-border rounded-sm text-xs uppercase tracking-wider hover:border-primary disabled:opacity-50"
          >
            {vaultBusy ? "Working..." : "Import Vault"}
          </button>
          <label className="px-4 py-2 border border-border rounded-sm text-xs uppercase tracking-wider hover:border-primary cursor-pointer">
            Load File
            <input
              type="file"
              accept="application/json,.json,.vault"
              className="hidden"
              onChange={(e) => {
                void handleVaultFile(e.target.files?.[0] || null);
              }}
            />
          </label>
        </div>

        <section className="space-y-2">
          <h3 className="text-[11px] text-muted-foreground uppercase tracking-wider">
            Vault Payload
          </h3>
          <textarea
            value={vaultPayload}
            onChange={(e) => setVaultPayload(e.target.value)}
            rows={8}
            className="w-full bg-input border border-border rounded-sm px-3 py-2 text-xs text-foreground placeholder:text-muted-foreground/50 outline-none focus:border-primary transition-colors resize-y"
            placeholder="Vault JSON payload (for import)..."
          />
        </section>

        {vaultStatus && <p className="text-xs text-muted-foreground">{vaultStatus}</p>}
      </section>
    </div>
  );
}
