import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../context/AuthContext";
import { useNavigate } from "react-router-dom";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { api } from "../../lib/api";
import { dispatchAgentProfileChanged } from "../../lib/events";

type AgentSelfModelSection = "identity" | "persona";

const SELF_MODEL_SECTIONS: Array<{
  key: AgentSelfModelSection;
  label: string;
  description: string;
  rows: number;
}> = [
  {
    key: "identity",
    label: "IDENTITY",
    description: "Who the agent understands itself to be.",
    rows: 7,
  },
  {
    key: "persona",
    label: "MAIN PERSONA",
    description: "The agent's main warmth, voice, and style in future replies.",
    rows: 9,
  },
];

const glass = "bg-background/25 backdrop-blur-[40px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.22)]";
const INPUT_CLASS = "w-full resize-y bg-foreground/[0.04] border border-foreground/[0.08] px-3 py-2 font-mono text-[11px] leading-relaxed text-foreground outline-none focus:border-foreground/[0.18] transition-colors placeholder:text-foreground/20 disabled:opacity-40";

export default function AgentProfileSettings() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [selfModelLoading, setSelfModelLoading] = useState(false);
  const [selfModelSaving, setSelfModelSaving] = useState<AgentSelfModelSection | null>(null);
  const [selfModelSaved, setSelfModelSaved] = useState<AgentSelfModelSection | null>(null);
  const [selfModelVersions, setSelfModelVersions] = useState<Record<AgentSelfModelSection, number | null>>({
    identity: null,
    persona: null,
  });
  const [selfModelDrafts, setSelfModelDrafts] = useState<Record<AgentSelfModelSection, string>>({
    identity: "",
    persona: "",
  });
  const [birthdayDraft, setBirthdayDraft] = useState("");
  const [birthdaySaving, setBirthdaySaving] = useState(false);
  const [birthdaySaved, setBirthdaySaved] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { agentName, relationship, agentType, avatarUrl, hasCustomAvatar } =
    useAgentProfile(user?.id);

  useEffect(() => {
    setBirthdayDraft(user?.birthday ?? "");
  }, [user?.birthday]);

  useEffect(() => {
    if (user?.id == null) {
      setSelfModelDrafts({ identity: "", persona: "" });
      setSelfModelVersions({ identity: null, persona: null });
      return;
    }
    let cancelled = false;
    setSelfModelLoading(true);
    setError("");

    Promise.all([
      api.consciousness.getSelfModelSection(user.id, "identity"),
      api.consciousness.getSelfModelSection(user.id, "persona"),
    ])
      .then(([identity, persona]) => {
        if (cancelled) return;
        setSelfModelDrafts({ identity: identity.content, persona: persona.content });
        setSelfModelVersions({ identity: identity.version, persona: persona.version });
      })
      .catch((err: any) => {
        if (cancelled) return;
        setError(err.message || "Failed to load identity and persona");
      })
      .finally(() => {
        if (!cancelled) setSelfModelLoading(false);
      });

    return () => { cancelled = true; };
  }, [user?.id]);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || user?.id == null) return;
    setUploading(true);
    setError("");
    try {
      await api.consciousness.uploadAgentAvatar(user.id, file);
      dispatchAgentProfileChanged();
    } catch (err: any) {
      setError(err.message || "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const handleRemoveAvatar = async () => {
    if (user?.id == null) return;
    setError("");
    try {
      await api.consciousness.deleteAgentAvatar(user.id);
      dispatchAgentProfileChanged();
    } catch (err: any) {
      setError(err.message || "Failed to remove avatar");
    }
  };

  const handleSelfModelSave = async (section: AgentSelfModelSection) => {
    if (user?.id == null || selfModelSaving != null) return;
    setSelfModelSaving(section);
    setSelfModelSaved(null);
    setError("");
    try {
      const updated = await api.consciousness.updateSelfModelSection(
        user.id,
        section,
        selfModelDrafts[section],
      );
      setSelfModelDrafts((current) => ({ ...current, [section]: updated.content }));
      setSelfModelVersions((current) => ({ ...current, [section]: updated.version }));
      setSelfModelSaved(section);
      window.setTimeout(() => {
        setSelfModelSaved((current) => (current === section ? null : current));
      }, 2000);
    } catch (err: any) {
      setError(err.message || `Failed to save ${section}`);
    } finally {
      setSelfModelSaving(null);
    }
  };

  const handleBirthdaySave = async () => {
    if (user?.id == null || birthdaySaving) return;
    setBirthdaySaving(true);
    setBirthdaySaved(false);
    setError("");
    try {
      await api.users.update(user.id, { birthday: birthdayDraft || null });
      setBirthdaySaved(true);
      window.setTimeout(() => setBirthdaySaved(false), 2000);
    } catch (err: any) {
      setError(err.message || "Failed to save birthday");
    } finally {
      setBirthdaySaving(false);
    }
  };

  const handleWarmerPersonaBaseline = async () => {
    if (user?.id == null || selfModelSaving != null) return;
    setSelfModelSaving("persona");
    setSelfModelSaved(null);
    setError("");
    try {
      await api.consciousness.updateAgentProfile(user.id, { personaTemplate: "companion" });
      const persona = await api.consciousness.getSelfModelSection(user.id, "persona");
      setSelfModelDrafts((current) => ({ ...current, persona: persona.content }));
      setSelfModelVersions((current) => ({ ...current, persona: persona.version }));
      setSelfModelSaved("persona");
      window.setTimeout(() => {
        setSelfModelSaved((current) => (current === "persona" ? null : current));
      }, 2000);
    } catch (err: any) {
      setError(err.message || "Failed to apply warmer persona");
    } finally {
      setSelfModelSaving(null);
    }
  };

  return (
    <div className="h-full overflow-y-auto pt-16">
      <div className="max-w-3xl mx-auto px-8 py-8 space-y-4">
        {/* Header */}
        <div className="flex items-center gap-4 pb-2">
          <button
            onClick={() => navigate(-1)}
            className="font-mono text-[9px] text-foreground/25 hover:text-foreground/55 tracking-[0.18em] uppercase transition-colors"
          >
            ← Back
          </button>
          <div className="h-3.5 w-px bg-foreground/[0.1]" />
          <div>
            <h2 className="font-mono text-[11px] tracking-[0.22em] uppercase text-foreground/60">
              {agentName || "Anima"}
            </h2>
            <p className="font-mono text-[9px] text-foreground/25 tracking-[0.2em] uppercase">
              Avatar, Identity & Persona
            </p>
          </div>
        </div>

        {/* Avatar */}
        <div className={`${glass} p-5 space-y-4`}>
          <div className="space-y-1">
            <h2 className="font-mono text-[9px] tracking-[0.22em] uppercase text-foreground/40">
              Avatar
            </h2>
            <p className="font-mono text-[10px] text-foreground/25 tracking-wide">
              PNG, JPG, WEBP, GIF or SVG — max 2 MB
            </p>
          </div>

          <div className="flex items-center gap-6">
            <img
              src={avatarUrl}
              alt="Agent avatar"
              className="w-20 h-20 border border-foreground/[0.1] object-cover flex-shrink-0"
            />
            <div className="flex flex-col gap-2">
              <input
                ref={fileInputRef}
                type="file"
                accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml"
                className="hidden"
                onChange={handleFileChange}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="font-mono text-[9px] tracking-[0.18em] uppercase px-4 py-2 border border-foreground/[0.1] text-foreground/40 hover:border-foreground/[0.2] hover:text-foreground/70 disabled:opacity-30 transition-all"
              >
                {uploading ? "Uploading..." : "Upload Image"}
              </button>
              <button
                onClick={handleRemoveAvatar}
                disabled={uploading || !hasCustomAvatar}
                className="font-mono text-[9px] tracking-[0.18em] uppercase px-4 py-2 border border-foreground/[0.07] text-foreground/25 hover:border-destructive/40 hover:text-destructive/70 disabled:opacity-30 transition-all"
              >
                Remove
              </button>
            </div>
          </div>
        </div>

        {/* Bio */}
        <div className={`${glass} p-5 space-y-4`}>
          <h2 className="font-mono text-[9px] tracking-[0.22em] uppercase text-foreground/40">
            Bio
          </h2>

          <div className="space-y-3 border-l border-foreground/[0.1] pl-4">
            <BioRow label="Name" value={agentName} />
            <BioRow label="Relationship" value={relationship} />
            {agentType && <BioRow label="Type" value={agentType} />}
          </div>
        </div>

        {/* Birthday */}
        <div className={`${glass} p-5 space-y-4`}>
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1">
              <h2 className="font-mono text-[9px] tracking-[0.22em] uppercase text-foreground/40">
                Your Birthday
              </h2>
              <p className="font-mono text-[10px] text-foreground/25 tracking-wide leading-relaxed">
                Used to give context-aware greetings on your special day.
              </p>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              {birthdaySaved && (
                <span className="font-mono text-[9px] text-accent/60 tracking-[0.18em] uppercase">
                  Saved
                </span>
              )}
              <button
                onClick={handleBirthdaySave}
                disabled={birthdaySaving}
                className="font-mono text-[9px] tracking-[0.18em] uppercase px-3 py-1.5 border border-foreground/[0.1] text-foreground/40 hover:border-foreground/[0.2] hover:text-foreground/70 disabled:opacity-30 transition-all"
              >
                {birthdaySaving ? "Saving..." : "Save"}
              </button>
            </div>
          </div>

          <input
            type="date"
            value={birthdayDraft}
            onChange={(e) => { setBirthdayDraft(e.target.value); setBirthdaySaved(false); }}
            className="bg-foreground/[0.04] border border-foreground/[0.08] px-3 py-2 font-mono text-[11px] text-foreground outline-none focus:border-foreground/[0.18] transition-colors [color-scheme:dark]"
          />
        </div>

        {/* Core identity */}
        <div className={`${glass} p-5 space-y-5`}>
          <div className="flex items-start justify-between gap-4">
            <div className="space-y-1">
              <h2 className="font-mono text-[9px] tracking-[0.22em] uppercase text-foreground/40">
                Core Identity
              </h2>
              <p className="font-mono text-[10px] text-foreground/25 tracking-wide leading-relaxed">
                Overwrite the agent identity and main persona used in future replies.
              </p>
            </div>
            {selfModelLoading && (
              <span className="font-mono text-[9px] text-foreground/20 tracking-[0.18em] uppercase shrink-0">
                Loading...
              </span>
            )}
          </div>

          <div className="space-y-5">
            {SELF_MODEL_SECTIONS.map((section) => (
              <div key={section.key} className="space-y-3 border-l border-foreground/[0.1] pl-4">
                <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                  <div className="space-y-0.5">
                    <h3 className="font-mono text-[9px] tracking-[0.18em] uppercase text-foreground/40">
                      {section.label}
                    </h3>
                    <p className="font-mono text-[10px] text-foreground/25 tracking-wide">
                      {section.description}
                    </p>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    {selfModelVersions[section.key] != null && (
                      <span className="font-mono text-[8px] text-foreground/20 tracking-wider">
                        v{selfModelVersions[section.key]}
                      </span>
                    )}
                    {selfModelSaved === section.key && (
                      <span className="font-mono text-[9px] text-accent/60 tracking-[0.18em] uppercase">
                        Saved
                      </span>
                    )}
                    {section.key === "persona" && (
                      <button
                        onClick={handleWarmerPersonaBaseline}
                        disabled={selfModelLoading || selfModelSaving !== null}
                        className="font-mono text-[9px] tracking-[0.14em] uppercase px-3 py-1.5 border border-accent/25 text-accent/50 hover:border-accent/50 hover:text-accent/80 disabled:opacity-30 transition-all"
                      >
                        Warmer Baseline
                      </button>
                    )}
                    <button
                      onClick={() => handleSelfModelSave(section.key)}
                      disabled={selfModelLoading || selfModelSaving !== null}
                      className="font-mono text-[9px] tracking-[0.18em] uppercase px-3 py-1.5 border border-foreground/[0.1] text-foreground/40 hover:border-foreground/[0.2] hover:text-foreground/70 disabled:opacity-30 transition-all"
                    >
                      {selfModelSaving === section.key ? "Saving..." : "Save"}
                    </button>
                  </div>
                </div>
                <textarea
                  value={selfModelDrafts[section.key]}
                  rows={section.rows}
                  disabled={selfModelLoading}
                  onChange={(e) => setSelfModelDrafts((current) => ({ ...current, [section.key]: e.target.value }))}
                  className={INPUT_CLASS}
                  placeholder={`${section.label.toLowerCase()} text`}
                />
              </div>
            ))}
          </div>
        </div>

        {error && (
          <p className="font-mono text-[10px] text-destructive/70 tracking-wider">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

function BioRow({ label, value }: { label: string; value?: string | null }) {
  return (
    <div>
      <div className="font-mono text-[9px] text-foreground/25 tracking-[0.18em] uppercase mb-0.5">
        {label}
      </div>
      <div className="font-mono text-sm text-foreground/70">
        {value || "—"}
      </div>
    </div>
  );
}
