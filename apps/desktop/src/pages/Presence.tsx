import { useEffect, useState, type ReactNode } from "react";
import type { PresenceConfig } from "@anima/api-client";
import { useAuth } from "../context/AuthContext";
import { api } from "../lib/api";

const DEFAULT_CONFIG: PresenceConfig = {
  userId: 0,
  enabled: true,
  mainChatEnabled: true,
  homeGreetingContextEnabled: true,
  taskNudgesEnabled: true,
  memoryNudgesEnabled: true,
  checkInNudgesEnabled: true,
  customInstruction: null,
  initiativeEnabled: false,
  quietHoursStart: null,
  quietHoursEnd: null,
  dreamSharing: "on_ask",
};

const SURFACE_OPTIONS = [
  {
    key: "mainChatEnabled",
    label: "Main Chat",
    detail: "Quiet notice above the composer",
  },
  {
    key: "homeGreetingContextEnabled",
    label: "Home Handoff",
    detail: "Greeting carries into the first reply",
  },
] as const;

const SIGNAL_OPTIONS = [
  {
    key: "taskNudgesEnabled",
    label: "Tasks",
    detail: "Deadlines and unfinished work",
  },
  {
    key: "memoryNudgesEnabled",
    label: "Memory",
    detail: "Open threads and held context",
  },
  {
    key: "checkInNudgesEnabled",
    label: "Check-ins",
    detail: "Conversation gaps",
  },
] as const;

const DREAM_SHARING_OPTIONS = [
  { value: "off", label: "Off" },
  { value: "on_ask", label: "On Ask" },
  { value: "ambient", label: "Ambient" },
] as const;

export default function Presence() {
  const { user } = useAuth();
  const [draft, setDraft] = useState<PresenceConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (user?.id == null) return;
    let active = true;

    setLoading(true);
    setError("");
    api.presence
      .get(user.id)
      .then((config) => {
        if (active) setDraft(config);
      })
      .catch((err) => {
        if (!active) return;
        setDraft({ ...DEFAULT_CONFIG, userId: user.id });
        setError(err instanceof Error ? err.message : "Failed to load presence.");
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
  const activeCount = [
    config.mainChatEnabled,
    config.homeGreetingContextEnabled,
    config.taskNudgesEnabled,
    config.memoryNudgesEnabled,
    config.checkInNudgesEnabled,
    config.initiativeEnabled,
  ].filter(Boolean).length;

  const updateDraft = (updates: Partial<PresenceConfig>) => {
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
      const next = await api.presence.update(user.id, {
        enabled: config.enabled,
        mainChatEnabled: config.mainChatEnabled,
        homeGreetingContextEnabled: config.homeGreetingContextEnabled,
        taskNudgesEnabled: config.taskNudgesEnabled,
        memoryNudgesEnabled: config.memoryNudgesEnabled,
        checkInNudgesEnabled: config.checkInNudgesEnabled,
        customInstruction: config.customInstruction || null,
        initiativeEnabled: config.initiativeEnabled,
        quietHoursStart: config.quietHoursStart,
        quietHoursEnd: config.quietHoursEnd,
        dreamSharing: config.dreamSharing,
      });
      setDraft(next);
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save presence.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto pt-16">
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-8 px-6 py-8">
        <header className="flex flex-col gap-4 border-b border-border pb-6 md:flex-row md:items-end md:justify-between">
          <div className="space-y-2">
            <h1 className="font-mono text-sm uppercase tracking-[0.18em]">
              PRESENCE
            </h1>
            <p className="font-mono text-[10px] uppercase tracking-[0.22em] text-muted-foreground/50">
              SURFACES, SIGNALS, AND DEFAULT INTENT
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground/55">
              {loading ? "Syncing" : `${activeCount}/6 Active`}
            </span>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || loading}
              className="border border-primary bg-input px-5 py-2 font-mono text-[10px] uppercase tracking-[0.18em] text-foreground transition-colors hover:bg-background disabled:opacity-45"
            >
              {saving ? "Saving" : saved ? "Saved" : "Save"}
            </button>
          </div>
        </header>

        <main className="grid gap-8 lg:grid-cols-[minmax(220px,0.65fr)_minmax(0,1.35fr)]">
          <aside className="space-y-4 border-b border-border pb-6 lg:border-b-0 lg:border-r lg:pb-0 lg:pr-6">
            <button
              type="button"
              onClick={() => updateDraft({ enabled: !config.enabled })}
              className={`group flex w-full items-center justify-between border px-4 py-4 text-left transition-colors ${
                config.enabled
                  ? "border-primary/70 bg-primary/5"
                  : "border-border bg-card"
              }`}
            >
              <span className="space-y-1">
                <span className="block text-sm text-foreground">
                  {config.enabled ? "Available" : "Paused"}
                </span>
                <span className="block font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground/50">
                  Master State
                </span>
              </span>
              <SwitchVisual enabled={config.enabled} />
            </button>

            <div className="space-y-2 border-t border-border pt-4">
              <StatusLine label="Main chat" enabled={config.enabled && config.mainChatEnabled} />
              <StatusLine
                label="Home handoff"
                enabled={config.enabled && config.homeGreetingContextEnabled}
              />
              <StatusLine label="Task signal" enabled={config.enabled && config.taskNudgesEnabled} />
              <StatusLine label="Memory signal" enabled={config.enabled && config.memoryNudgesEnabled} />
              <StatusLine
                label="Check-in signal"
                enabled={config.enabled && config.checkInNudgesEnabled}
              />
              <StatusLine
                label="Initiative"
                enabled={config.enabled && config.initiativeEnabled}
              />
            </div>
          </aside>

          <div className="space-y-8">
            <ControlGroup title="Surfaces">
              {SURFACE_OPTIONS.map((option) => (
                <SwitchRow
                  key={option.key}
                  label={option.label}
                  detail={option.detail}
                  checked={config[option.key]}
                  disabled={!config.enabled}
                  onChange={(checked) => updateDraft({ [option.key]: checked })}
                />
              ))}
            </ControlGroup>

            <ControlGroup title="Signals">
              {SIGNAL_OPTIONS.map((option) => (
                <SwitchRow
                  key={option.key}
                  label={option.label}
                  detail={option.detail}
                  checked={config[option.key]}
                  disabled={!config.enabled}
                  onChange={(checked) => updateDraft({ [option.key]: checked })}
                />
              ))}
            </ControlGroup>

            <ControlGroup title="Initiative">
              <SwitchRow
                label="Unprompted Messages"
                detail="May reach out when a drive crosses its threshold"
                checked={config.initiativeEnabled}
                disabled={!config.enabled}
                onChange={(checked) => updateDraft({ initiativeEnabled: checked })}
              />
              <div className="flex items-center justify-between gap-4 px-1 py-4">
                <span className="min-w-0 space-y-1">
                  <span className="block text-sm text-foreground">Quiet Hours</span>
                  <span className="block text-xs text-muted-foreground">
                    No messages inside this window — set both, to different hours, to enable
                  </span>
                </span>
                <span className="flex items-center gap-2">
                  <HourSelect
                    value={config.quietHoursStart}
                    disabled={!config.enabled || !config.initiativeEnabled}
                    onChange={(value) => updateDraft({ quietHoursStart: value })}
                  />
                  <span className="font-mono text-[10px] text-muted-foreground/40">
                    TO
                  </span>
                  <HourSelect
                    value={config.quietHoursEnd}
                    disabled={!config.enabled || !config.initiativeEnabled}
                    onChange={(value) => updateDraft({ quietHoursEnd: value })}
                  />
                </span>
              </div>
              <div className="flex items-center justify-between gap-4 px-1 py-4">
                <span className="min-w-0 space-y-1">
                  <span className="block text-sm text-foreground">Dream Sharing</span>
                  <span className="block text-xs text-muted-foreground">
                    Whether night reflections may surface
                  </span>
                </span>
                <span className="flex border border-border">
                  {DREAM_SHARING_OPTIONS.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      disabled={!config.enabled}
                      onClick={() => updateDraft({ dreamSharing: option.value })}
                      className={`px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.16em] transition-colors disabled:opacity-45 ${
                        config.dreamSharing === option.value
                          ? "bg-primary/15 text-foreground"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {option.label}
                    </button>
                  ))}
                </span>
              </div>
            </ControlGroup>

            <section className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <h2 className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
                  Default Direction
                </h2>
                <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground/40">
                  {(config.customInstruction ?? "").length}/500
                </span>
              </div>
              <textarea
                value={config.customInstruction ?? ""}
                onChange={(event) =>
                  updateDraft({ customInstruction: event.currentTarget.value })
                }
                rows={4}
                maxLength={500}
                placeholder="mention Tappy gently"
                className="w-full resize-none border border-border bg-input px-3 py-3 text-sm leading-relaxed text-foreground outline-none transition-colors placeholder:text-muted-foreground/30 focus:border-primary"
              />
              <div className="min-h-5">
                {error && <p className="text-xs text-destructive">{error}</p>}
                {!error && saved && <p className="text-xs text-primary">Saved</p>}
              </div>
            </section>
          </div>
        </main>
      </div>
    </div>
  );
}

function ControlGroup({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-3">
      <h2 className="font-mono text-[11px] uppercase tracking-[0.18em] text-muted-foreground">
        {title}
      </h2>
      <div className="divide-y divide-border border-y border-border">
        {children}
      </div>
    </section>
  );
}

function StatusLine({ label, enabled }: { label: string; enabled: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${
          enabled ? "bg-primary" : "bg-muted-foreground/25"
        }`}
      />
    </div>
  );
}

function SwitchRow({
  label,
  detail,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  detail: string;
  checked: boolean;
  disabled: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="flex w-full items-center justify-between gap-4 px-1 py-4 text-left transition-colors hover:text-foreground disabled:opacity-45"
    >
      <span className="min-w-0 space-y-1">
        <span className="block text-sm text-foreground">{label}</span>
        <span className="block text-xs text-muted-foreground">{detail}</span>
      </span>
      <SwitchVisual enabled={checked} />
    </button>
  );
}

function SwitchVisual({ enabled }: { enabled: boolean }) {
  return (
    <span
      className={`relative h-5 w-9 shrink-0 border transition-colors ${
        enabled ? "border-primary bg-primary/15" : "border-border bg-background"
      }`}
      aria-hidden="true"
    >
      <span
        className={`absolute top-1/2 h-3 w-3 -translate-y-1/2 bg-current transition-transform ${
          enabled ? "translate-x-4 text-primary" : "translate-x-1 text-muted-foreground/40"
        }`}
      />
    </span>
  );
}

function HourSelect({
  value,
  disabled,
  onChange,
}: {
  value: number | null;
  disabled: boolean;
  onChange: (value: number | null) => void;
}) {
  return (
    <select
      value={value == null ? "" : String(value)}
      disabled={disabled}
      onChange={(event) => {
        const raw = event.currentTarget.value;
        onChange(raw === "" ? null : Number(raw));
      }}
      className="border border-border bg-input px-2 py-1.5 font-mono text-xs text-foreground outline-none transition-colors focus:border-primary disabled:opacity-45"
    >
      <option value="">—</option>
      {Array.from({ length: 24 }, (_, hour) => (
        <option key={hour} value={hour}>
          {String(hour).padStart(2, "0")}:00
        </option>
      ))}
    </select>
  );
}
