import { useEffect, useState } from "react";
import { useAuth } from "../../context/AuthContext";
import { api, type AgentConfig, type ProviderInfo } from "../../lib/api";

const SUGGESTED_MODELS: Record<string, string[]> = {
  ollama: [
    "vaultbox/qwen3.5-uncensored:35b",
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

const FALLBACK_PROVIDERS: ProviderInfo[] = [
  { name: "ollama", defaultModel: "vaultbox/qwen3.5-uncensored:35b", requiresApiKey: false },
  { name: "openrouter", defaultModel: "openrouter/free", requiresApiKey: true },
  { name: "openai", defaultModel: "gpt-4o", requiresApiKey: true },
  { name: "anthropic", defaultModel: "claude-sonnet-4-20250514", requiresApiKey: true },
];

const INPUT_CLASS =
  "w-full bg-bg-input border border-border rounded-sm px-3 py-2 text-sm text-text placeholder:text-text-muted/50 outline-none focus:border-primary transition-colors";

export default function AiSettings() {
  const { user } = useAuth();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [provider, setProvider] = useState("ollama");
  const [model, setModel] = useState("vaultbox/qwen3.5-uncensored:35b");
  const [apiKey, setApiKey] = useState("");
  const [ollamaUrl, setOllamaUrl] = useState("http://localhost:11434");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (user?.id == null) return;

    let cancelled = false;
    setError("");

    Promise.all([api.config.providers(), api.config.get(user.id)])
      .then(([providerList, loadedConfig]) => {
        if (cancelled) return;
        setProviders(providerList);
        setConfig(loadedConfig);
        setProvider(loadedConfig.provider);
        setModel(loadedConfig.model);
        setOllamaUrl(loadedConfig.ollamaUrl || "http://localhost:11434");
        setSystemPrompt(loadedConfig.systemPrompt || "");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "Failed to load AI settings.");
      });

    return () => {
      cancelled = true;
    };
  }, [user?.id]);

  if (user?.id == null) {
    return null;
  }

  const providerOptions = providers.length > 0 ? providers : FALLBACK_PROVIDERS;
  const selectedProvider = providerOptions.find((item) => item.name === provider);
  const suggestions = SUGGESTED_MODELS[provider] || [];
  const requiresKey = selectedProvider?.requiresApiKey ?? provider !== "ollama";

  const handleProviderChange = (nextProvider: string) => {
    setProvider(nextProvider);
    const defaults = SUGGESTED_MODELS[nextProvider];
    if (defaults?.length) {
      setModel(defaults[0]);
    } else {
      const providerInfo = providerOptions.find((item) => item.name === nextProvider);
      if (providerInfo?.defaultModel) {
        setModel(providerInfo.defaultModel);
      }
    }
    setApiKey("");
  };

  const handleSave = async () => {
    if (user?.id == null) return;

    setSaving(true);
    setSaved(false);
    setError("");

    try {
      await api.config.update(user.id, {
        provider,
        model,
        apiKey: apiKey || undefined,
        ollamaUrl,
        systemPrompt: systemPrompt || undefined,
      });
      setConfig({
        provider,
        model,
        ollamaUrl,
        systemPrompt: systemPrompt || null,
        hasApiKey: config?.hasApiKey || Boolean(apiKey),
      });
      setApiKey("");
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save AI settings.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-6">
      <section className="rounded-sm border border-border bg-bg-card p-5 space-y-5">
        <header className="space-y-1">
          <h2 className="text-[11px] text-text-muted uppercase tracking-wider">
            Inference Provider
          </h2>
          <p className="text-xs text-text-muted">
            Choose the runtime that powers chat, summarization, and agent orchestration.
          </p>
        </header>

        <div className="grid grid-cols-2 gap-2">
          {providerOptions.map((item) => (
            <button
              key={item.name}
              onClick={() => handleProviderChange(item.name)}
              className={`px-3 py-2 text-xs uppercase tracking-wider rounded-sm border transition-colors ${
                provider === item.name
                  ? "border-primary text-text bg-bg-input"
                  : "border-border text-text-muted hover:border-text-muted"
              }`}
            >
              {item.name}
            </button>
          ))}
        </div>

        <Field label="Model">
          <input
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className={INPUT_CLASS}
            placeholder="Model identifier..."
          />
          {suggestions.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {suggestions.map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => setModel(suggestion)}
                  className={`text-[10px] px-2 py-0.5 rounded-sm border transition-colors ${
                    model === suggestion
                      ? "border-primary text-text"
                      : "border-border text-text-muted hover:text-text"
                  }`}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          )}
        </Field>

        {requiresKey && (
          <Field label="API Key">
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className={INPUT_CLASS}
              placeholder={
                config?.hasApiKey ? "Key saved - enter new value to replace" : "Enter API key..."
              }
            />
            {config?.hasApiKey && !apiKey && (
              <p className="mt-1 text-[10px] text-text-muted">Key stored</p>
            )}
          </Field>
        )}

        {provider === "ollama" && (
          <Field label="Ollama Endpoint">
            <input
              type="text"
              value={ollamaUrl}
              onChange={(e) => setOllamaUrl(e.target.value)}
              className={INPUT_CLASS}
            />
          </Field>
        )}

        <Field label="System Directive Override">
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={5}
            className={`${INPUT_CLASS} resize-none`}
            placeholder="Custom system prompt (leave empty for default)..."
          />
        </Field>

        <div className="flex items-center gap-4">
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-5 py-2 bg-bg-input border border-primary text-text text-xs uppercase tracking-wider rounded-sm hover:bg-bg disabled:opacity-50 transition-colors"
          >
            {saving ? "Saving..." : "Save AI Settings"}
          </button>
          {saved && <span className="text-xs text-primary tracking-wider">Saved</span>}
          {error && <span className="text-xs text-danger tracking-wider">{error}</span>}
        </div>
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
