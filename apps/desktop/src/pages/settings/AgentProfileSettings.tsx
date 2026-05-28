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

export default function AgentProfileSettings() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [selfModelLoading, setSelfModelLoading] = useState(false);
  const [selfModelSaving, setSelfModelSaving] =
    useState<AgentSelfModelSection | null>(null);
  const [selfModelSaved, setSelfModelSaved] =
    useState<AgentSelfModelSection | null>(null);
  const [selfModelVersions, setSelfModelVersions] = useState<
    Record<AgentSelfModelSection, number | null>
  >({
    identity: null,
    persona: null,
  });
  const [selfModelDrafts, setSelfModelDrafts] = useState<
    Record<AgentSelfModelSection, string>
  >({
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
        setSelfModelDrafts({
          identity: identity.content,
          persona: persona.content,
        });
        setSelfModelVersions({
          identity: identity.version,
          persona: persona.version,
        });
      })
      .catch((err: any) => {
        if (cancelled) return;
        setError(err.message || "Failed to load identity and persona");
      })
      .finally(() => {
        if (!cancelled) setSelfModelLoading(false);
      });

    return () => {
      cancelled = true;
    };
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
      setSelfModelDrafts((current) => ({
        ...current,
        [section]: updated.content,
      }));
      setSelfModelVersions((current) => ({
        ...current,
        [section]: updated.version,
      }));
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
      await api.consciousness.updateAgentProfile(user.id, {
        personaTemplate: "companion",
      });
      const persona = await api.consciousness.getSelfModelSection(
        user.id,
        "persona",
      );
      setSelfModelDrafts((current) => ({
        ...current,
        persona: persona.content,
      }));
      setSelfModelVersions((current) => ({
        ...current,
        persona: persona.version,
      }));
      setSelfModelSaved("persona");
      window.setTimeout(() => {
        setSelfModelSaved((current) =>
          current === "persona" ? null : current,
        );
      }, 2000);
    } catch (err: any) {
      setError(err.message || "Failed to apply warmer persona");
    } finally {
      setSelfModelSaving(null);
    }
  };

  return (
    <div className="h-full overflow-y-auto pt-16">
      <div className="max-w-3xl mx-auto px-8 py-8 space-y-8">
        {/* Header */}
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate(-1)}
            className="font-mono text-[9px] text-muted-foreground/40 hover:text-muted-foreground tracking-wider transition-colors"
          >
            ← BACK
          </button>
          <div className="space-y-0.5">
            <h2 className="font-mono text-sm tracking-wider">
              {agentName || "ANIMA"}
            </h2>
            <p className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
              AVATAR, IDENTITY AND MAIN PERSONA
            </p>
          </div>
        </div>
        {/* Avatar */}
        <section className="space-y-4">
          <div className="space-y-1">
            <h2 className="font-mono text-[10px] tracking-wider text-foreground">
              AVATAR
            </h2>
            <p className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
              PNG, JPG, WEBP, GIF or SVG — max 2 MB
            </p>
          </div>

          <div className="flex items-center gap-6">
            <img
              src={avatarUrl}
              alt="Agent avatar"
              className="w-20 h-20 rounded-none border border-border object-cover flex-shrink-0"
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
                className="font-mono text-[10px] tracking-wider px-4 py-2 border border-border text-muted-foreground hover:text-foreground hover:border-primary transition-colors disabled:opacity-40"
              >
                {uploading ? "UPLOADING..." : "UPLOAD IMAGE"}
              </button>
              <button
                onClick={handleRemoveAvatar}
                disabled={uploading || !hasCustomAvatar}
                className="font-mono text-[10px] tracking-wider px-4 py-2 border border-border text-muted-foreground/50 hover:text-destructive hover:border-destructive transition-colors disabled:opacity-30"
              >
                REMOVE
              </button>
            </div>
          </div>
        </section>

        {/* Bio */}
        <section className="space-y-4">
          <h2 className="font-mono text-[10px] tracking-wider text-foreground">
            BIO
          </h2>
          <div className="space-y-3 border-l-2 border-border pl-4">
            <div>
              <div className="font-mono text-[9px] text-muted-foreground/40 tracking-wider mb-0.5">
                NAME
              </div>
              <div className="font-mono text-sm text-foreground">
                {agentName || "—"}
              </div>
            </div>
            <div>
              <div className="font-mono text-[9px] text-muted-foreground/40 tracking-wider mb-0.5">
                RELATIONSHIP
              </div>
              <div className="font-mono text-sm text-foreground">
                {relationship || "—"}
              </div>
            </div>
            {agentType && (
              <div>
                <div className="font-mono text-[9px] text-muted-foreground/40 tracking-wider mb-0.5">
                  TYPE
                </div>
                <div className="font-mono text-sm text-foreground">
                  {agentType}
                </div>
              </div>
            )}
          </div>
        </section>

        {/* Birthday */}
        <section className="space-y-4">
          <div className="flex items-end justify-between gap-4">
            <div className="space-y-1">
              <h2 className="font-mono text-[10px] tracking-wider text-foreground">
                YOUR BIRTHDAY
              </h2>
              <p className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                USED TO GIVE CONTEXT-AWARE GREETINGS ON YOUR SPECIAL DAY.
              </p>
            </div>
            <div className="flex items-center gap-3">
              {birthdaySaved && (
                <span className="font-mono text-[8px] text-primary tracking-wider">
                  SAVED
                </span>
              )}
              <button
                onClick={handleBirthdaySave}
                disabled={birthdaySaving}
                className="font-mono px-3 py-1.5 text-[9px] tracking-wider border border-border text-muted-foreground hover:text-foreground hover:border-primary disabled:opacity-30 transition-colors"
              >
                {birthdaySaving ? "SAVING..." : "SAVE"}
              </button>
            </div>
          </div>
          <input
            type="date"
            value={birthdayDraft}
            onChange={(e) => {
              setBirthdayDraft(e.target.value);
              setBirthdaySaved(false);
            }}
            className="border border-border bg-input px-3 py-2 font-mono text-[11px] text-foreground outline-none transition-colors focus:border-primary/50 [color-scheme:dark]"
          />
        </section>

        {/* Direct self-model */}
        <section className="space-y-4">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <div className="space-y-1">
              <h2 className="font-mono text-[10px] tracking-wider text-foreground">
                CORE IDENTITY
              </h2>
              <p className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                OVERWRITE THE AGENT IDENTITY AND MAIN PERSONA USED IN FUTURE REPLIES.
              </p>
            </div>
            {selfModelLoading && (
              <span className="font-mono text-[9px] text-muted-foreground/30 tracking-wider">
                LOADING...
              </span>
            )}
          </div>

          <div className="space-y-5">
            {SELF_MODEL_SECTIONS.map((section) => (
              <div
                key={section.key}
                className="space-y-3 border-l-2 border-border pl-4"
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div className="space-y-1">
                    <h3 className="font-mono text-[10px] tracking-wider text-foreground">
                      {section.label}
                    </h3>
                    <p className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                      {section.description}
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    {selfModelVersions[section.key] != null && (
                      <span className="font-mono text-[8px] text-muted-foreground/30 tracking-wider">
                        V{selfModelVersions[section.key]}
                      </span>
                    )}
                    {selfModelSaved === section.key && (
                      <span className="font-mono text-[8px] text-primary tracking-wider">
                        SAVED
                      </span>
                    )}
                    {section.key === "persona" && (
                      <button
                        onClick={handleWarmerPersonaBaseline}
                        disabled={selfModelLoading || selfModelSaving !== null}
                        className="font-mono px-3 py-1.5 text-[9px] tracking-wider border border-primary/30 text-primary/70 hover:text-primary hover:border-primary disabled:opacity-30 transition-colors"
                      >
                        WARMER BASELINE
                      </button>
                    )}
                    <button
                      onClick={() => handleSelfModelSave(section.key)}
                      disabled={selfModelLoading || selfModelSaving !== null}
                      className="font-mono px-3 py-1.5 text-[9px] tracking-wider border border-border text-muted-foreground hover:text-foreground hover:border-primary disabled:opacity-30 transition-colors"
                    >
                      {selfModelSaving === section.key ? "SAVING..." : "SAVE"}
                    </button>
                  </div>
                </div>
                <textarea
                  value={selfModelDrafts[section.key]}
                  rows={section.rows}
                  disabled={selfModelLoading}
                  onChange={(e) =>
                    setSelfModelDrafts((current) => ({
                      ...current,
                      [section.key]: e.target.value,
                    }))
                  }
                  className="w-full resize-y border border-border bg-background/60 px-3 py-2 font-mono text-[11px] leading-relaxed text-foreground outline-none transition-colors placeholder:text-muted-foreground/30 focus:border-primary/50 focus:bg-card/30 disabled:opacity-40"
                  placeholder={`${section.label.toLowerCase()} text`}
                />
              </div>
            ))}
          </div>
        </section>

        {error && (
          <p className="font-mono text-[10px] text-destructive tracking-wider">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}
