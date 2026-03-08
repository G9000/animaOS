import { useState, useEffect } from "react";
import { useAuth } from "../context/AuthContext";
import { api, type ProviderInfo, type AgentConfig } from "../lib/api";

const SUGGESTED_MODELS: Record<string, string[]> = {
  ollama: [
    "qwen3:14b",
    "gemma3:12b",
    "deepseek-r1:32b",
    "devstral:latest",
    "mistral:latest",
    "llama4:latest",
  ],
  openai: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
  anthropic: [
    "claude-sonnet-4-20250514",
    "claude-3-5-haiku-20241022",
    "claude-3-opus-20240229",
  ],
  openrouter: [
    "openrouter/free",
    "google/gemini-2.5-flash-lite",
    "google/gemini-2.5-flash",
    "google/gemini-3-flash-preview",
    "google/gemini-3.1-flash-lite-preview",
  ],
};

export default function Settings() {
  const { user } = useAuth();
  const [, setProviders] = useState<ProviderInfo[]>([]);
  const [config, setConfig] = useState<AgentConfig | null>(null);

  const [provider, setProvider] = useState("ollama");
  const [model, setModel] = useState("llama3.1:8b");
  const [apiKey, setApiKey] = useState("");
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [systemPrompt, setSystemPrompt] = useState("");

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [vaultPassphrase, setVaultPassphrase] = useState("");
  const [vaultPayload, setVaultPayload] = useState("");
  const [vaultBusy, setVaultBusy] = useState(false);
  const [vaultStatus, setVaultStatus] = useState("");

  useEffect(() => {
    if (!user?.id) return;
    Promise.all([api.config.providers(), api.config.get(user.id)]).then(
      ([provs, cfg]) => {
        setProviders(provs);
        setConfig(cfg);
        setProvider(cfg.provider);
        setModel(cfg.model);
        setOllamaUrl(cfg.ollamaUrl || "http://localhost:11434");
        setSystemPrompt(cfg.systemPrompt || "");
      },
    );
  }, [user?.id]);

  const handleProviderChange = (newProvider: string) => {
    setProvider(newProvider);
    const defaults = SUGGESTED_MODELS[newProvider];
    if (defaults?.length) setModel(defaults[0]);
    setApiKey("");
  };

  const handleSave = async () => {
    if (!user?.id) return;
    setSaving(true);
    setError("");
    setSaved(false);

    try {
      await api.config.update(user.id, {
        provider,
        model,
        apiKey: apiKey || undefined,
        ollamaUrl,
        systemPrompt: systemPrompt || undefined,
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  };

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
      const a = document.createElement("a");
      a.href = url;
      a.download = result.filename;
      a.click();
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

  const requiresKey = provider !== "ollama";
  const suggestions = SUGGESTED_MODELS[provider] || [];

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-xl mx-auto px-8 py-8 space-y-8">
        <h1 className="text-sm tracking-widest uppercase">
          Configuration
        </h1>

        {/* Provider Selection */}
        <section>
          <h2 className="text-[11px] text-(--color-text-muted) uppercase tracking-wider mb-3">
            Inference Provider
          </h2>
          <div className="grid grid-cols-2 gap-2">
            {["ollama", "openrouter", "openai", "anthropic"].map((p) => (
              <button
                key={p}
                onClick={() => handleProviderChange(p)}
                className={`px-3 py-2 text-xs uppercase tracking-wider rounded-sm border transition-colors ${
                  provider === p
                    ? "border-(--color-primary) text-(--color-text) bg-(--color-bg-card)"
                    : "border-(--color-border) text-(--color-text-muted) hover:border-(--color-text-muted)"
                }`}
              >
                {p}
              </button>
            ))}
          </div>
        </section>

        {/* Model */}
        <section>
          <h2 className="text-[11px] text-(--color-text-muted) uppercase tracking-wider mb-3">
            Model
          </h2>
          <input
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="w-full bg-(--color-bg-input) border border-(--color-border) rounded-sm px-3 py-2 text-sm text-(--color-text) placeholder:text-(--color-text-muted)/50 outline-none focus:border-(--color-primary) transition-colors"
            placeholder="Model identifier..."
          />
          {suggestions.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  onClick={() => setModel(s)}
                  className={`text-[10px] px-2 py-0.5 rounded-sm border transition-colors ${
                    model === s
                      ? "border-(--color-primary) text-(--color-text)"
                      : "border-(--color-border) text-(--color-text-muted) hover:text-(--color-text)"
                  }`}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
        </section>

        {/* API Key */}
        {requiresKey && (
          <section>
            <h2 className="text-[11px] text-(--color-text-muted) uppercase tracking-wider mb-3">
              API Key
            </h2>
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="w-full bg-(--color-bg-input) border border-(--color-border) rounded-sm px-3 py-2 text-sm text-(--color-text) placeholder:text-(--color-text-muted)/50 outline-none focus:border-(--color-primary) transition-colors"
              placeholder={
                config?.hasApiKey
                  ? "Key saved — enter new to replace"
                  : "Enter API key..."
              }
            />
            {config?.hasApiKey && !apiKey && (
              <p className="text-[10px] text-(--color-text-muted) mt-1">
                Key stored
              </p>
            )}
          </section>
        )}

        {/* Ollama URL */}
        {provider === "ollama" && (
          <section>
            <h2 className="text-[11px] text-(--color-text-muted) uppercase tracking-wider mb-3">
              Ollama Endpoint
            </h2>
            <input
              type="text"
              value={ollamaUrl}
              onChange={(e) => setOllamaUrl(e.target.value)}
              className="w-full bg-(--color-bg-input) border border-(--color-border) rounded-sm px-3 py-2 text-sm text-(--color-text) placeholder:text-(--color-text-muted)/50 outline-none focus:border-(--color-primary) transition-colors"
            />
          </section>
        )}

        {/* System Prompt */}
        <section>
          <h2 className="text-[11px] text-(--color-text-muted) uppercase tracking-wider mb-3">
            System Directive Override
          </h2>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={4}
            className="w-full bg-(--color-bg-input) border border-(--color-border) rounded-sm px-3 py-2 text-sm text-(--color-text) placeholder:text-(--color-text-muted)/50 outline-none focus:border-(--color-primary) transition-colors resize-none"
            placeholder="Custom system prompt (leave empty for default)..."
          />
        </section>

        {/* Save */}
        <div className="flex items-center gap-4">
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-5 py-2 bg-(--color-bg-card) border border-(--color-primary) text-(--color-text) text-xs uppercase tracking-wider rounded-sm hover:bg-(--color-bg-input) disabled:opacity-50 transition-colors"
          >
            {saving ? "Saving..." : "Save"}
          </button>

          {saved && (
            <span className="text-xs text-(--color-primary) tracking-wider">
              Saved
            </span>
          )}

          {error && (
            <span className="text-xs text-(--color-danger) tracking-wider">
              {error}
            </span>
          )}
        </div>

        {/* Vault */}
        <section className="pt-6 border-t border-(--color-border) space-y-3">
          <h2 className="text-[11px] text-(--color-text-muted) uppercase tracking-wider">
            Vault Backup
          </h2>
          <input
            type="password"
            value={vaultPassphrase}
            onChange={(e) => setVaultPassphrase(e.target.value)}
            className="w-full bg-(--color-bg-input) border border-(--color-border) rounded-sm px-3 py-2 text-sm text-(--color-text) placeholder:text-(--color-text-muted)/50 outline-none focus:border-(--color-primary) transition-colors"
            placeholder="Vault passphrase (min 8 chars)"
          />
          <div className="flex gap-2">
            <button
              onClick={handleVaultExport}
              disabled={vaultBusy}
              className="px-4 py-2 border border-(--color-border) rounded-sm text-xs uppercase tracking-wider hover:border-(--color-primary) disabled:opacity-50"
            >
              {vaultBusy ? "Working..." : "Export Vault"}
            </button>
            <button
              onClick={handleVaultImport}
              disabled={vaultBusy}
              className="px-4 py-2 border border-(--color-border) rounded-sm text-xs uppercase tracking-wider hover:border-(--color-primary) disabled:opacity-50"
            >
              {vaultBusy ? "Working..." : "Import Vault"}
            </button>
            <label className="px-4 py-2 border border-(--color-border) rounded-sm text-xs uppercase tracking-wider hover:border-(--color-primary) cursor-pointer">
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
          <textarea
            value={vaultPayload}
            onChange={(e) => setVaultPayload(e.target.value)}
            rows={5}
            className="w-full bg-(--color-bg-input) border border-(--color-border) rounded-sm px-3 py-2 text-xs text-(--color-text) placeholder:text-(--color-text-muted)/50 outline-none focus:border-(--color-primary) transition-colors resize-y"
            placeholder="Vault JSON payload (for import)..."
          />
          {vaultStatus && (
            <p className="text-xs text-(--color-text-muted)">{vaultStatus}</p>
          )}
        </section>
      </div>
    </div>
  );
}
