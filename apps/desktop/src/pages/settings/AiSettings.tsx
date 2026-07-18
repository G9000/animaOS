import { useEffect, useState } from "react";
import { useAuth } from "../../context/AuthContext";
import type { AgentConfig, OllamaModelInfo, ProviderInfo } from "@anima/api-client";
import { api } from "../../lib/api";
import { cn, EyeIcon, EyeOffIcon } from "@anima/standard-templates";
import { getProviderLogo } from "./_provider-logos";

const OLLAMA_DEFAULT_URL = "http://localhost:11434";

const SUGGESTED_MODELS: Record<string, string[]> = {
  ollama: ["gemma4:31b", "vaultbox/qwen3.5-uncensored:35b", "qwen3:14b", "gemma3:12b", "deepseek-r1:32b", "devstral:latest", "mistral:latest", "llama4:latest"],
  openai: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
  anthropic: ["claude-haiku-4-5-20251001", "claude-haiku-4-5", "claude-3-opus-20240229"],
  openrouter: ["openrouter/free", "google/gemini-2.5-flash-lite", "google/gemini-2.5-flash", "google/gemini-3-flash-preview"],
  doubleword: ["Qwen/Qwen3.6-35B-A3B-FP8", "Qwen/Qwen3.5-35B-A3B-FP8"],
};

const FALLBACK_PROVIDERS: ProviderInfo[] = [
  { name: "ollama",     defaultModel: "vaultbox/qwen3.5-uncensored:35b", requiresApiKey: false },
  { name: "vllm",       defaultModel: "default",                          requiresApiKey: false },
  { name: "openrouter", defaultModel: "openrouter/free",                  requiresApiKey: true },
  { name: "doubleword", defaultModel: "Qwen/Qwen3.6-35B-A3B-FP8",        requiresApiKey: true },
  { name: "openai",     defaultModel: "gpt-4o",                           requiresApiKey: true },
  { name: "anthropic",  defaultModel: "claude-haiku-4-5-20251001",        requiresApiKey: true },
];

// Embedding-capable providers only — openrouter/anthropic have no embeddings
// endpoint (server skips them entirely) and scaffold is chat-only, so none
// of the three are offered here. fastembed IS offered here even though it's
// excluded from the chat provider list above (it has no chat completion).
const EMBEDDING_PROVIDERS = ["fastembed", "ollama", "openai", "vllm", "doubleword"] as const;
// Must mirror the server's local/no-key-required embedding providers — see
// apps/server/.../services/agent/embeddings.py::_validate_embedding_provider_
// configuration, which only requires an embedding API key for
// openrouter/moonshot/openai/doubleword (openrouter/moonshot aren't even
// offered here, see EMBEDDING_PROVIDERS above). fastembed (in-process ONNX,
// no HTTP endpoint) and ollama/vllm (local OpenAI-compatible servers with a
// default localhost base URL, no key) are the local/no-consent-needed set;
// openai/doubleword are cloud and gated behind the Cloud toggle above.
const EMBEDDING_LOCAL_PROVIDERS = new Set(["fastembed", "ollama", "vllm"]);
const EMBEDDING_DEFAULT_MODELS: Record<string, string> = {
  fastembed: "BAAI/bge-small-en-v1.5",
  ollama: "nomic-embed-text",
  openai: "text-embedding-3-small",
  vllm: "text-embedding-3-small",
  doubleword: "Qwen/Qwen3-Embedding-8B",
};
const BUNDLED_EMBEDDING_MODEL = EMBEDDING_DEFAULT_MODELS.fastembed;

const LOCAL_PROVIDERS = new Set(["ollama", "vllm"]);
const CLOUD_STORAGE_KEY = "anima:cloud-providers-enabled";
const keyHintKey = (p: string) => `anima:key-hint:${p}`;
const getKeyHint = (p: string) => localStorage.getItem(keyHintKey(p));
const setKeyHint = (p: string, key: string) => { if (key.length >= 4) localStorage.setItem(keyHintKey(p), key.slice(-4)); };
const clearKeyHint = (p: string) => localStorage.removeItem(keyHintKey(p));

const PROVIDER_KEY_HINTS: Record<string, string> = {
  openai:     "platform.openai.com/api-keys",
  anthropic:  "console.anthropic.com/settings/keys",
  openrouter: "openrouter.ai/keys",
  doubleword: "Contact your Doubleword account for an API key.",
};

const glass = "bg-background/25 backdrop-blur-[40px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.22)]";
const INPUT = "w-full bg-foreground/[0.04] border border-foreground/[0.08] px-3 py-2.5 text-sm text-foreground placeholder:text-foreground/25 outline-none focus:border-foreground/[0.18] transition-colors font-mono";

export default function AiSettings() {
  const { user } = useAuth();
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [provider, setProvider] = useState("ollama");
  const [model, setModel] = useState("vaultbox/qwen3.5-uncensored:35b");
  const [extractionModel, setExtractionModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [ollamaUrl, setOllamaUrl] = useState(OLLAMA_DEFAULT_URL);
  const [ollamaModels, setOllamaModels] = useState<OllamaModelInfo[]>([]);
  const [ollamaModelsLoading, setOllamaModelsLoading] = useState(false);
  const [ollamaModelsError, setOllamaModelsError] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [cloudEnabled, setCloudEnabled] = useState(() => localStorage.getItem(CLOUD_STORAGE_KEY) === "true");
  const [modelSearch, setModelSearch] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [replacingKey, setReplacingKey] = useState(false);
  const [embeddingAdvanced, setEmbeddingAdvanced] = useState(false);
  const [embeddingProvider, setEmbeddingProvider] = useState("fastembed");
  const [embeddingModel, setEmbeddingModel] = useState(BUNDLED_EMBEDDING_MODEL);
  const [embeddingApiKey, setEmbeddingApiKey] = useState("");
  const [showEmbeddingApiKey, setShowEmbeddingApiKey] = useState(false);
  const [replacingEmbeddingKey, setReplacingEmbeddingKey] = useState(false);

  const loadOllamaModels = async (baseUrl: string) => {
    setOllamaModelsLoading(true);
    setOllamaModelsError("");
    try { setOllamaModels(await api.config.ollamaModels(baseUrl)); }
    catch (err) { setOllamaModels([]); setOllamaModelsError(err instanceof Error ? err.message : "Failed to load models."); }
    finally { setOllamaModelsLoading(false); }
  };

  useEffect(() => {
    if (user?.id == null) return;
    let cancelled = false;
    Promise.all([api.config.providers(), api.config.get(user.id)])
      .then(([providerList, loadedConfig]) => {
        if (cancelled) return;
        setProviders(providerList);
        setConfig(loadedConfig);
        setProvider(loadedConfig.provider);
        if (!LOCAL_PROVIDERS.has(loadedConfig.provider) || !EMBEDDING_LOCAL_PROVIDERS.has(loadedConfig.embeddingProvider)) { setCloudEnabled(true); localStorage.setItem(CLOUD_STORAGE_KEY, "true"); }
        setModel(loadedConfig.model);
        setExtractionModel(loadedConfig.extractionModel || "");
        setOllamaUrl(loadedConfig.ollamaUrl || OLLAMA_DEFAULT_URL);
        setSystemPrompt(loadedConfig.systemPrompt || "");
        setEmbeddingAdvanced(loadedConfig.embeddingIsExplicit);
        setEmbeddingProvider(loadedConfig.embeddingProvider);
        setEmbeddingModel(loadedConfig.embeddingModel);
      })
      .catch((err) => { if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load AI settings."); });
    return () => { cancelled = true; };
  }, [user?.id]);

  useEffect(() => {
    if (provider !== "ollama") return;
    void loadOllamaModels(config?.ollamaUrl || OLLAMA_DEFAULT_URL);
  }, [provider, config?.ollamaUrl]);

  if (user?.id == null) return null;

  const providerOptions = (providers.length > 0 ? providers : FALLBACK_PROVIDERS).filter((p) => p.name !== "scaffold");
  const localProviders = providerOptions.filter((p) => LOCAL_PROVIDERS.has(p.name));
  const cloudProviders = providerOptions.filter((p) => !LOCAL_PROVIDERS.has(p.name));
  const selectedProvider = providerOptions.find((item) => item.name === provider);
  const modelSuggestions = provider === "ollama" ? ollamaModels.map((m) => m.name) : SUGGESTED_MODELS[provider] || [];
  const requiresKey = selectedProvider?.requiresApiKey ?? provider !== "ollama";
  const isCloudProvider = !LOCAL_PROVIDERS.has(provider);
  const providerHasKey = (config?.hasApiKey && config.provider === provider) || Boolean(getKeyHint(provider));
  const embeddingProviderHasKey = Boolean(config?.hasEmbeddingApiKey) && config?.embeddingProvider === embeddingProvider;

  const handleEnableCloud = () => { setCloudEnabled(true); localStorage.setItem(CLOUD_STORAGE_KEY, "true"); };
  const handleDisableCloud = () => {
    setCloudEnabled(false); localStorage.removeItem(CLOUD_STORAGE_KEY);
    if (isCloudProvider) handleProviderChange(localProviders[0]?.name || "ollama");
    if (!EMBEDDING_LOCAL_PROVIDERS.has(embeddingProvider)) handleEmbeddingProviderChange("fastembed");
  };
  const handleProviderChange = (next: string) => {
    setProvider(next);
    const defaults = SUGGESTED_MODELS[next];
    if (defaults?.length) { setModel(defaults[0]); }
    else { const info = providerOptions.find((item) => item.name === next); if (info?.defaultModel) setModel(info.defaultModel); }
    setApiKey(""); setReplacingKey(false); setSaved(false); setError(""); setModelSearch("");
    if (next === "ollama") void loadOllamaModels(ollamaUrl || OLLAMA_DEFAULT_URL);
  };

  const handleEmbeddingProviderChange = (next: string) => {
    setEmbeddingProvider(next);
    setEmbeddingModel(EMBEDDING_DEFAULT_MODELS[next] || "");
    setEmbeddingApiKey(""); setReplacingEmbeddingKey(false); setSaved(false); setError("");
  };

  const buildEmbeddingUpdate = () =>
    embeddingAdvanced
      ? {
          embeddingProvider,
          embeddingModel: embeddingModel || undefined,
          embeddingApiKey: embeddingApiKey || undefined,
        }
      : { embeddingProvider: "" };

  const applyRefreshedConfig = async () => {
    if (user?.id == null) return;
    const refreshed = await api.config.get(user.id);
    setConfig(refreshed);
    setEmbeddingAdvanced(refreshed.embeddingIsExplicit);
    setEmbeddingProvider(refreshed.embeddingProvider);
    setEmbeddingModel(refreshed.embeddingModel);
  };

  const handleSave = async () => {
    if (user?.id == null) return;
    setSaving(true); setSaved(false); setError("");
    try {
      await api.config.update(user.id, {
        provider, model,
        extractionModel: extractionModel || undefined,
        apiKey: apiKey || undefined,
        ollamaUrl,
        systemPrompt: systemPrompt || undefined,
        ...buildEmbeddingUpdate(),
      });
      if (apiKey) setKeyHint(provider, apiKey);
      await applyRefreshedConfig();
      setApiKey(""); setReplacingKey(false);
      setEmbeddingApiKey(""); setReplacingEmbeddingKey(false);
      setSaved(true); setTimeout(() => setSaved(false), 2500);
    } catch (err) { setError(err instanceof Error ? err.message : "Failed to save."); }
    finally { setSaving(false); }
  };

  const handleRemoveEmbeddingKey = async () => {
    if (user?.id == null) return;
    setSaving(true); setSaved(false); setError("");
    try {
      await api.config.update(user.id, {
        provider, model,
        extractionModel: extractionModel || undefined,
        ollamaUrl,
        systemPrompt: systemPrompt || undefined,
        embeddingProvider,
        embeddingModel: embeddingModel || undefined,
        embeddingApiKey: "",
      });
      // Re-fetch rather than optimistically assuming hasEmbeddingApiKey is
      // now false: _resolve_embedding_api_key() may still resolve a key for
      // this provider from the per-provider store or an env var (e.g.
      // DOUBLEWORD_API_KEY) even after the dedicated field is cleared, so
      // the backend's real resolved state — not local optimism — must
      // drive the UI (get_config's hasEmbeddingApiKey, commit c7c9b10).
      await applyRefreshedConfig();
      setSaved(true); setTimeout(() => setSaved(false), 2500);
    } catch (err) { setError(err instanceof Error ? err.message : "Failed to remove key."); }
    finally { setSaving(false); }
  };

  const handleRemoveKey = async () => {
    if (user?.id == null) return;
    setSaving(true); setSaved(false); setError("");
    try {
      await api.config.update(user.id, { provider, model, extractionModel: extractionModel || undefined, apiKey: "", ollamaUrl, systemPrompt: systemPrompt || undefined });
      clearKeyHint(provider);
      setConfig((prev) => prev ? { ...prev, hasApiKey: false } : prev);
      setSaved(true); setTimeout(() => setSaved(false), 2500);
    } catch (err) { setError(err instanceof Error ? err.message : "Failed to remove key."); }
    finally { setSaving(false); }
  };

  return (
    <div className="space-y-3">

      {/* ── Provider ── */}
      <div className={`${glass} overflow-visible`}>
        <div className="px-5 pt-4 pb-3 border-b border-foreground/[0.08] flex items-center justify-between">
          <h2 className="font-mono text-[9px] tracking-[0.26em] uppercase text-foreground/40">Inference Provider</h2>
          <div className="flex items-center gap-2.5">
            <span className="font-mono text-[9px] tracking-[0.14em] uppercase text-foreground/40">
              Cloud {cloudEnabled ? "on" : "off"}
            </span>
            <MiniToggle checked={cloudEnabled} onChange={(v) => { if (v) handleEnableCloud(); else handleDisableCloud(); }} />
          </div>
        </div>

        <div className="p-4 overflow-visible">
          <div className="grid grid-cols-4 xl:grid-cols-5 gap-2 overflow-visible">
            {localProviders.map((item) => (
              <ProviderCard key={item.name} name={item.name} badge="local" active={provider === item.name} onClick={() => handleProviderChange(item.name)} />
            ))}
            {cloudProviders.map((item) => (
              <ProviderCard
                key={item.name} name={item.name} badge="cloud"
                active={provider === item.name}
                disabled={!cloudEnabled}
                onClick={() => cloudEnabled && handleProviderChange(item.name)}
              />
            ))}
          </div>
          {isCloudProvider ? (
            <div className="mt-3 border border-amber-500/20 bg-amber-500/[0.06] px-4 py-3 flex items-start gap-3">
              <svg className="shrink-0 mt-0.5 text-amber-400/70" width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                <path d="M8 2L1.5 13.5h13L8 2z" />
                <path d="M8 7v3M8 11.5v.5" />
              </svg>
              <div className="space-y-1.5">
                <p className="font-mono text-[10px] text-amber-300/80 tracking-[0.12em] uppercase font-semibold">
                  Cloud provider active — {provider}
                </p>
                <p className="font-mono text-[9px] text-foreground/55 tracking-wide leading-relaxed">
                  Your messages are processed by {provider}'s servers.{" "}
                  {EMBEDDING_LOCAL_PROVIDERS.has(embeddingProvider)
                    ? "Everything else — your memories, vault, diary, and personal data — never leaves this device and remains fully encrypted."
                    : `Your embedding provider (${embeddingProvider}) is also cloud-based — see the Embeddings card below for what that sends off-device.`}
                </p>
              </div>
            </div>
          ) : !cloudEnabled && (
            <p className="font-mono text-[9px] text-foreground/30 tracking-wide mt-3 leading-relaxed">
              Enable cloud to unlock hosted models. Only messages are sent — memories stay encrypted on-device.
            </p>
          )}
        </div>
      </div>

      {/* ── Embeddings ── */}
      <div className={`${glass}`}>
        <div className="px-5 pt-4 pb-3 border-b border-foreground/[0.08] flex items-center justify-between">
          <h2 className="font-mono text-[9px] tracking-[0.26em] uppercase text-foreground/40">Embeddings</h2>
          <div className="flex items-center gap-2.5">
            <span className="font-mono text-[9px] tracking-[0.14em] uppercase text-foreground/40">
              Advanced {embeddingAdvanced ? "on" : "off"}
            </span>
            <MiniToggle checked={embeddingAdvanced} onChange={(v) => { setEmbeddingAdvanced(v); setSaved(false); if (!v) { setEmbeddingApiKey(""); setReplacingEmbeddingKey(false); } }} />
          </div>
        </div>

        <div className="p-4 space-y-3">
          {!embeddingAdvanced ? (
            <p className="font-mono text-[9px] text-foreground/40 tracking-wide leading-relaxed">
              Built-in (recommended) — {BUNDLED_EMBEDDING_MODEL}, runs on this device.
            </p>
          ) : (
            <>
              <div className="space-y-2">
                <label className="font-mono text-[8px] tracking-[0.22em] uppercase text-foreground/30">Provider</label>
                <select
                  value={embeddingProvider}
                  onChange={(e) => {
                    const next = e.target.value;
                    if (cloudEnabled || EMBEDDING_LOCAL_PROVIDERS.has(next)) handleEmbeddingProviderChange(next);
                  }}
                  className={INPUT}
                >
                  {EMBEDDING_PROVIDERS.map((p) => (
                    <option key={p} value={p} disabled={!cloudEnabled && !EMBEDDING_LOCAL_PROVIDERS.has(p)}>
                      {p}{EMBEDDING_LOCAL_PROVIDERS.has(p) ? " (local)" : " (cloud)"}{p === "fastembed" ? " — built-in" : ""}
                      {!cloudEnabled && !EMBEDDING_LOCAL_PROVIDERS.has(p) ? " — enable cloud to use" : ""}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <label className="font-mono text-[8px] tracking-[0.22em] uppercase text-foreground/30">Model</label>
                <input
                  type="text"
                  value={embeddingModel}
                  onChange={(e) => { setEmbeddingModel(e.target.value); setSaved(false); }}
                  className={INPUT}
                  placeholder={EMBEDDING_DEFAULT_MODELS[embeddingProvider] || "Embedding model identifier..."}
                />
                <p className="font-mono text-[8px] text-foreground/20 leading-relaxed">
                  Changing the provider or model re-checks the embedding contract; existing memories are re-embedded automatically.
                </p>
              </div>
              {!EMBEDDING_LOCAL_PROVIDERS.has(embeddingProvider) && (
                <div className="border border-amber-500/20 bg-amber-500/[0.06] px-4 py-3 flex items-start gap-3">
                  <svg className="shrink-0 mt-0.5 text-amber-400/70" width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M8 2L1.5 13.5h13L8 2z" />
                    <path d="M8 7v3M8 11.5v.5" />
                  </svg>
                  <div className="space-y-1.5">
                    <p className="font-mono text-[10px] text-amber-300/80 tracking-[0.12em] uppercase font-semibold">
                      Cloud embedding provider active — {embeddingProvider}
                    </p>
                    <p className="font-mono text-[9px] text-foreground/55 tracking-wide leading-relaxed">
                      Unlike chat, this sends more than messages: the text of your memories and document chunks is transmitted to {embeddingProvider}'s servers to be embedded. This overrides the "memories stay encrypted on-device" guarantee shown above for anything that gets embedded with this provider.
                    </p>
                  </div>
                </div>
              )}
              {!EMBEDDING_LOCAL_PROVIDERS.has(embeddingProvider) && (
                <div className="space-y-2">
                  <label className="font-mono text-[8px] tracking-[0.22em] uppercase text-foreground/30">API Key</label>
                  {embeddingProviderHasKey && !replacingEmbeddingKey ? (
                    <div className="flex items-center justify-between border border-foreground/[0.08] px-4 py-3">
                      <span className="font-mono text-[10px] text-foreground/40 tracking-wide">sk-·····················</span>
                      <div className="flex items-center gap-3">
                        <button type="button" onClick={() => setReplacingEmbeddingKey(true)}
                          className="font-mono text-[8px] tracking-[0.16em] uppercase text-foreground/40 hover:text-foreground/70 transition-colors">
                          Change
                        </button>
                        <button type="button" onClick={handleRemoveEmbeddingKey} disabled={saving}
                          className="font-mono text-[8px] tracking-[0.16em] uppercase text-destructive/50 hover:text-destructive/80 disabled:opacity-30 transition-colors">
                          Remove
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      <div className="relative">
                        <input
                          type={showEmbeddingApiKey ? "text" : "password"}
                          value={embeddingApiKey}
                          onChange={(e) => { setEmbeddingApiKey(e.target.value); setSaved(false); }}
                          className={`${INPUT} pr-9`}
                          placeholder="Paste your API key..."
                          autoComplete="off"
                          autoFocus={replacingEmbeddingKey}
                        />
                        <button type="button" onClick={() => setShowEmbeddingApiKey((v) => !v)}
                          className={cn(
                            "absolute right-3 top-1/2 -translate-y-1/2 transition-colors",
                            showEmbeddingApiKey ? "text-accent hover:text-accent/70" : "text-foreground/25 hover:text-foreground/55",
                          )}>
                          {showEmbeddingApiKey ? <EyeOffIcon size="sm" /> : <EyeIcon size="sm" />}
                        </button>
                      </div>
                      {replacingEmbeddingKey && (
                        <button type="button" onClick={() => { setReplacingEmbeddingKey(false); setEmbeddingApiKey(""); }}
                          className="font-mono text-[8px] tracking-[0.14em] uppercase text-foreground/30 hover:text-foreground/60 transition-colors">
                          Cancel
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* ── Model — full width, 3-zone ── */}
      <div className={`${glass}`}>
        <div className="px-5 pt-4 pb-3 border-b border-foreground/[0.08] flex items-center justify-between">
          <h2 className="font-mono text-[9px] tracking-[0.26em] uppercase text-foreground/40">Model</h2>
          {provider === "ollama" && (
            <button
              onClick={() => void loadOllamaModels(ollamaUrl || OLLAMA_DEFAULT_URL)}
              className="flex items-center gap-1.5 font-mono text-[8px] tracking-wider uppercase text-foreground/30 hover:text-foreground/60 transition-colors"
            >
              {ollamaModelsLoading ? "Loading..." : `${ollamaModels.length} installed · Refresh`}
            </button>
          )}
        </div>

        <div className="flex flex-col">
          <div className="px-4 py-2 border-b border-foreground/[0.04] flex items-center gap-2">
            <svg className="shrink-0 text-foreground/20" width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="6.5" cy="6.5" r="4.5" /><path d="M11 11l3 3" />
            </svg>
            <input
              type="text"
              value={modelSearch}
              onChange={(e) => setModelSearch(e.target.value)}
              placeholder={provider === "ollama" ? "Filter installed..." : "Filter models..."}
              className="flex-1 bg-transparent font-mono text-[9px] text-foreground/70 placeholder:text-foreground/20 outline-none"
            />
            {modelSearch && (
              <button onClick={() => setModelSearch("")} className="font-mono text-[8px] text-foreground/20 hover:text-foreground/50 transition-colors">✕</button>
            )}
          </div>
          <div className="overflow-y-auto max-h-[11rem]">
            {modelSuggestions.length > 0 ? (
              <ModelList
                provider={provider}
                ollamaModels={ollamaModels}
                suggestions={modelSuggestions}
                search={modelSearch}
                activeModel={model}
                onSelect={setModel}
              />
            ) : (
              <div className="px-4 py-5 space-y-1.5">
                <p className="font-mono text-[8px] text-foreground/25 mb-2">No suggestions — enter a model identifier manually.</p>
                <input
                  type="text"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  className={INPUT}
                  placeholder="Model identifier..."
                />
              </div>
            )}
            {ollamaModelsError && (
              <p className="px-4 py-2 font-mono text-[9px] text-destructive/70">{ollamaModelsError}</p>
            )}
          </div>
        </div>
      </div>

      {/* ── Connection / BYOK ── */}
      {(requiresKey || provider === "ollama") && (
        <div className={`${glass}`}>
            <div className="px-5 pt-4 pb-3 border-b border-foreground/[0.05] flex items-center gap-2.5">
              <span className="text-foreground/35 shrink-0">{getProviderLogo(provider)}</span>
              <h2 className="font-mono text-[9px] tracking-[0.26em] uppercase text-foreground/40 flex-1">
                {requiresKey ? "API Key" : "Endpoint"}
              </h2>
            </div>

            <div className="p-4 space-y-3">
              {requiresKey && (
                <>
                  {providerHasKey && !replacingKey ? (
                    /* ── Saved state ── */
                    <div className="flex items-center justify-between border border-foreground/[0.08] px-4 py-3">
                      <span className="font-mono text-[10px] text-foreground/40 tracking-wide">
                        {(() => { const hint = getKeyHint(provider); return hint ? `sk-···············${hint}` : `sk-·····················`; })()}
                      </span>
                      <div className="flex items-center gap-3">
                        <button type="button" onClick={() => setReplacingKey(true)}
                          className="font-mono text-[8px] tracking-[0.16em] uppercase text-foreground/40 hover:text-foreground/70 transition-colors">
                          Change
                        </button>
                        <button type="button" onClick={handleRemoveKey} disabled={saving}
                          className="font-mono text-[8px] tracking-[0.16em] uppercase text-destructive/50 hover:text-destructive/80 disabled:opacity-30 transition-colors">
                          Remove
                        </button>
                      </div>
                    </div>
                  ) : (
                    /* ── Input (no key yet or replacing) ── */
                    <div className="space-y-2">
                      <div className="relative">
                        <input
                          type={showApiKey ? "text" : "password"}
                          value={apiKey}
                          onChange={(e) => setApiKey(e.target.value)}
                          className={`${INPUT} pr-9`}
                          placeholder="Paste your API key..."
                          autoComplete="off"
                          autoFocus={replacingKey}
                        />
                        <button type="button" onClick={() => setShowApiKey((v) => !v)}
                          className={cn(
                            "absolute right-3 top-1/2 -translate-y-1/2 transition-colors",
                            showApiKey ? "text-accent hover:text-accent/70" : "text-foreground/25 hover:text-foreground/55",
                          )}>
                          {showApiKey ? <EyeOffIcon size="sm" /> : <EyeIcon size="sm" />}
                        </button>
                      </div>
                      {replacingKey ? (
                        <button type="button" onClick={() => { setReplacingKey(false); setApiKey(""); }}
                          className="font-mono text-[8px] tracking-[0.14em] uppercase text-foreground/30 hover:text-foreground/60 transition-colors">
                          Cancel
                        </button>
                      ) : PROVIDER_KEY_HINTS[provider] && (
                        <p className="font-mono text-[8px] text-foreground/30 leading-relaxed">
                          Get your key at <span className="text-foreground/50">{PROVIDER_KEY_HINTS[provider]}</span>
                        </p>
                      )}
                    </div>
                  )}
                </>
              )}

              {provider === "ollama" && (
                <div className="space-y-2">
                  <label className="font-mono text-[8px] tracking-[0.22em] uppercase text-foreground/30">Base URL</label>
                  <input
                    type="text"
                    value={ollamaUrl}
                    onChange={(e) => setOllamaUrl(e.target.value)}
                    className={INPUT}
                    placeholder={OLLAMA_DEFAULT_URL}
                  />
                  <p className="font-mono text-[8px] text-foreground/20 leading-relaxed">
                    Defaults to localhost. Change if Ollama runs on a remote machine.
                  </p>
                </div>
              )}
            </div>
          </div>
        )}

      {/* ── Save ── */}
      <div className={`${glass} px-5 py-3 flex items-center justify-between`}>
        <div>
          {saved && <span className="font-mono text-[9px] text-accent tracking-[0.18em] uppercase">✓ Saved</span>}
          {error && <span className="font-mono text-[9px] text-destructive/70 tracking-wide">{error}</span>}
        </div>
        <button
          onClick={handleSave}
          disabled={saving}
          className="font-mono text-[9px] tracking-[0.2em] uppercase px-6 py-2.5 bg-accent text-background font-semibold hover:opacity-90 disabled:opacity-30 transition-all"
        >
          {saving ? "Saving..." : "Save Settings"}
        </button>
      </div>
    </div>
  );
}

function MiniToggle({ checked, onChange }: { checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer border transition-all duration-200",
        checked ? "border-accent/50 bg-accent/[0.12]" : "border-foreground/[0.15] bg-foreground/[0.04]",
      )}
    >
      <span className={cn(
        "pointer-events-none absolute top-0.5 h-3.5 w-3.5 transition-all duration-200",
        checked ? "left-[18px] bg-accent" : "left-0.5 bg-foreground/30",
      )} />
    </button>
  );
}

function ModelList({ provider, ollamaModels, suggestions, search, activeModel, onSelect }: {
  provider: string;
  ollamaModels: OllamaModelInfo[];
  suggestions: string[];
  search: string;
  activeModel: string;
  onSelect: (m: string) => void;
}) {
  const q = search.toLowerCase();
  const logo = getProviderLogo(provider);
  if (provider === "ollama") {
    const filtered = ollamaModels.filter((m) => m.name.toLowerCase().includes(q));
    if (filtered.length === 0) return <p className="px-4 py-4 font-mono text-[8px] text-foreground/25">No models match "{search}"</p>;
    const sorted = [...filtered].sort((a, b) => (b.name === activeModel ? 1 : 0) - (a.name === activeModel ? 1 : 0));
    return <>{sorted.map((m) => <ModelRow key={m.name} name={m.name} size={m.size ?? undefined} logo={logo} active={activeModel === m.name} onClick={() => onSelect(m.name)} />)}</>;
  }
  const filtered = suggestions.filter((s) => s.toLowerCase().includes(q));
  if (filtered.length === 0) return <p className="px-4 py-4 font-mono text-[8px] text-foreground/25">No models match "{search}"</p>;
  const sorted = [...filtered].sort((a, b) => (b === activeModel ? 1 : 0) - (a === activeModel ? 1 : 0));
  return <>{sorted.map((s) => <ModelRow key={s} name={s} logo={logo} active={activeModel === s} onClick={() => onSelect(s)} />)}</>;
}

function formatModelSize(bytes: number): string {
  if (bytes >= 1_000_000_000) return `${(bytes / 1_000_000_000).toFixed(1)} GB`;
  if (bytes >= 1_000_000) return `${(bytes / 1_000_000).toFixed(0)} MB`;
  return `${Math.round(bytes / 1_000)} KB`;
}

function ModelRow({ name, size, logo, active, onClick }: { name: string; size?: number; logo?: React.ReactNode; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "group w-full flex items-center gap-2.5 pl-3 pr-3 py-2.5 text-left transition-all duration-150 border-l-[3px]",
        active
          ? "bg-accent border-accent-dark"
          : "border-transparent hover:bg-foreground/[0.05] hover:border-foreground/[0.15]",
      )}
    >
      {logo && (
        <span className={cn(
          "shrink-0 transition-colors",
          active ? "text-background" : "text-foreground/30 group-hover:text-foreground/55",
        )}>
          {logo}
        </span>
      )}
      <span className={cn(
        "font-mono text-[9px] tracking-wide truncate flex-1 transition-colors",
        active ? "text-background font-semibold" : "text-foreground/55 group-hover:text-foreground/85",
      )}>
        {name}
      </span>
      {size != null && (
        <span className={cn(
          "font-mono text-[8px] shrink-0 tabular-nums",
          active ? "text-background/55" : "text-foreground/20",
        )}>
          {formatModelSize(size)}
        </span>
      )}
    </button>
  );
}

function ProviderCard({ name, badge, active, disabled, onClick }: { name: string; badge: "local" | "cloud"; active: boolean; disabled?: boolean; onClick: () => void }) {
  const logo = getProviderLogo(name);
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "relative flex flex-col items-center justify-center gap-2 py-3.5 px-2 border transition-all duration-150 overflow-visible",
        active
          ? "bg-accent border-accent z-10"
          : disabled
          ? "border-foreground/[0.05] text-foreground/20 cursor-not-allowed"
          : "border-foreground/[0.08] hover:border-foreground/[0.2] hover:bg-foreground/[0.04]",
      )}
      style={active ? {
        transform: "translateX(6px)",
        boxShadow: "-2px 2px 0 var(--color-accent-dark), -4px 4px 0 color-mix(in oklch, var(--color-accent-dark) 55%, transparent)",
      } : undefined}
      onMouseEnter={(e) => { if (!active && !disabled) { const el = e.currentTarget; el.style.transform = "translateX(3px)"; el.style.boxShadow = "-2px 2px 0 rgba(0,0,0,0.25), -3px 3px 0 rgba(0,0,0,0.12)"; } }}
      onMouseLeave={(e) => { if (!active && !disabled) { const el = e.currentTarget; el.style.transform = ""; el.style.boxShadow = ""; } }}
    >
      <span className={cn("transition-colors", active ? "text-background" : disabled ? "text-foreground/20" : "text-foreground/50")}>
        {logo}
      </span>
      <span className={cn(
        "font-mono text-[10px] tracking-[0.1em] uppercase leading-none font-medium",
        active ? "text-background" : disabled ? "text-foreground/20" : "text-foreground/65",
      )}>
        {name}
      </span>
      <span className={cn(
        "font-mono text-[7px] tracking-[0.2em] uppercase px-1.5 py-0.5 border leading-none",
        active
          ? "border-background/30 text-background/60"
          : "border-foreground/[0.08] text-foreground/25",
      )}>
        {badge}
      </span>
    </button>
  );
}
