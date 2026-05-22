import { useEffect, useState } from "react";
import type { ProactivityConfig } from "@anima/api-client";
import { useAuth } from "../context/AuthContext";
import { api } from "../lib/api";

const DEFAULT_CONFIG: ProactivityConfig = {
  userId: 0,
  enabled: true,
  mainChatEnabled: true,
  homeGreetingContextEnabled: true,
  taskNudgesEnabled: true,
  memoryNudgesEnabled: true,
  checkInNudgesEnabled: true,
  customInstruction: null,
};

export default function Proactivity() {
  const { user } = useAuth();
  const [draft, setDraft] = useState<ProactivityConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (user?.id == null) return;
    let active = true;

    setLoading(true);
    setError("");
    api.proactivity
      .get(user.id)
      .then((config) => {
        if (active) setDraft(config);
      })
      .catch((err) => {
        if (!active) return;
        setDraft({ ...DEFAULT_CONFIG, userId: user.id });
        setError(
          err instanceof Error ? err.message : "Failed to load proactivity.",
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [user?.id]);

  if (user?.id == null) {
    return null;
  }

  const config = draft ?? { ...DEFAULT_CONFIG, userId: user.id };

  const updateDraft = (updates: Partial<ProactivityConfig>) => {
    setDraft((current) => ({
      ...(current ?? { ...DEFAULT_CONFIG, userId: user.id }),
      ...updates,
    }));
    setSaved(false);
  };

  const handleSave = async () => {
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const next = await api.proactivity.update(user.id, {
        enabled: config.enabled,
        mainChatEnabled: config.mainChatEnabled,
        homeGreetingContextEnabled: config.homeGreetingContextEnabled,
        taskNudgesEnabled: config.taskNudgesEnabled,
        memoryNudgesEnabled: config.memoryNudgesEnabled,
        checkInNudgesEnabled: config.checkInNudgesEnabled,
        customInstruction: config.customInstruction || null,
      });
      setDraft(next);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to save proactivity.",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
        <header className="space-y-2">
          <h1 className="font-mono text-sm tracking-[0.18em] uppercase">
            PROACTIVITY
          </h1>
          <p className="font-mono text-[10px] text-muted-foreground/50 tracking-[0.22em] uppercase">
            SURFACES, SIGNALS, AND DEFAULT INTENT
          </p>
        </header>

        <section className="rounded-sm border border-border bg-card p-5 space-y-5">
          <header className="flex items-center justify-between gap-4">
            <div className="space-y-1">
              <h2 className="text-[11px] text-muted-foreground uppercase tracking-wider">
                Runtime
              </h2>
              <p className="text-xs text-muted-foreground">
                {config.enabled ? "Proactivity is active." : "Proactivity is paused."}
              </p>
            </div>
            <ToggleRow
              label="Enabled"
              checked={config.enabled}
              onChange={(enabled) => updateDraft({ enabled })}
              compact
            />
          </header>

          <div className="grid gap-3 md:grid-cols-2">
            <ToggleRow
              label="Main chat notice"
              detail="Show a quiet prompt above the chat input."
              checked={config.mainChatEnabled}
              disabled={!config.enabled}
              onChange={(mainChatEnabled) => updateDraft({ mainChatEnabled })}
            />
            <ToggleRow
              label="Home greeting handoff"
              detail="Carry the home greeting into the first chat reply."
              checked={config.homeGreetingContextEnabled}
              disabled={!config.enabled}
              onChange={(homeGreetingContextEnabled) =>
                updateDraft({ homeGreetingContextEnabled })
              }
            />
          </div>
        </section>

        <section className="rounded-sm border border-border bg-card p-5 space-y-5">
          <header className="space-y-1">
            <h2 className="text-[11px] text-muted-foreground uppercase tracking-wider">
              Signals
            </h2>
          </header>

          <div className="grid gap-3 md:grid-cols-3">
            <ToggleRow
              label="Tasks"
              detail="Overdue tasks and near deadlines."
              checked={config.taskNudgesEnabled}
              disabled={!config.enabled}
              onChange={(taskNudgesEnabled) =>
                updateDraft({ taskNudgesEnabled })
              }
            />
            <ToggleRow
              label="Memory"
              detail="Open threads and working memory."
              checked={config.memoryNudgesEnabled}
              disabled={!config.enabled}
              onChange={(memoryNudgesEnabled) =>
                updateDraft({ memoryNudgesEnabled })
              }
            />
            <ToggleRow
              label="Check-ins"
              detail="Gaps since the last conversation."
              checked={config.checkInNudgesEnabled}
              disabled={!config.enabled}
              onChange={(checkInNudgesEnabled) =>
                updateDraft({ checkInNudgesEnabled })
              }
            />
          </div>
        </section>

        <section className="rounded-sm border border-border bg-card p-5 space-y-4">
          <header className="space-y-1">
            <h2 className="text-[11px] text-muted-foreground uppercase tracking-wider">
              Default Custom Notice
            </h2>
          </header>

          <textarea
            value={config.customInstruction ?? ""}
            onChange={(event) =>
              updateDraft({ customInstruction: event.currentTarget.value })
            }
            rows={4}
            maxLength={500}
            placeholder="e.g. mention Tappy gently"
            className="w-full resize-none rounded-sm border border-border bg-input px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/35 outline-none focus:border-primary"
          />
          <div className="flex items-center justify-between gap-4">
            <p className="font-mono text-[10px] tracking-[0.16em] uppercase text-muted-foreground/45">
              {(config.customInstruction ?? "").length}/500
            </p>
            <div className="flex items-center gap-3">
              {loading && (
                <span className="text-xs text-muted-foreground">Loading...</span>
              )}
              {saved && <span className="text-xs text-primary">Saved</span>}
              {error && <span className="text-xs text-destructive">{error}</span>}
              <button
                type="button"
                onClick={handleSave}
                disabled={saving || loading}
                className="rounded-sm border border-primary bg-input px-5 py-2 text-xs uppercase tracking-wider text-foreground transition-colors hover:bg-background disabled:opacity-50"
              >
                {saving ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

function ToggleRow({
  label,
  detail,
  checked,
  disabled = false,
  compact = false,
  onChange,
}: {
  label: string;
  detail?: string;
  checked: boolean;
  disabled?: boolean;
  compact?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label
      className={`flex items-center justify-between gap-4 border border-border bg-background/40 ${
        compact ? "px-3 py-2" : "p-4"
      } ${disabled ? "opacity-45" : "cursor-pointer hover:border-muted-foreground/40"}`}
    >
      <span className="min-w-0 space-y-1">
        <span className="block text-sm text-foreground">{label}</span>
        {detail && (
          <span className="block text-xs leading-relaxed text-muted-foreground">
            {detail}
          </span>
        )}
      </span>
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.currentTarget.checked)}
        className="h-4 w-4 shrink-0 cursor-pointer accent-primary disabled:cursor-default"
      />
    </label>
  );
}
