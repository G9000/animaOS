import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../context/AuthContext";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { API_BASE } from "../../lib/runtime";
import { getUnlockToken } from "../../lib/api";
import personaAvatar from "../../assets/persona-default.svg";

export default function AgentProfileSettings() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const [avatarUrl, setAvatarUrl] = useState<string>(personaAvatar);
  const [agentName, setAgentName] = useState("");
  const [relationship, setRelationship] = useState("");
  const [agentType, setAgentType] = useState("");
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (user?.id == null) return;
    let revoked = false;

    api.consciousness
      .getAgentProfile(user.id)
      .then(async (profile) => {
        setAgentName(profile.agentName ?? "");
        setRelationship(profile.relationship ?? "");
        setAgentType(profile.agentType ?? "");

        if (!profile.avatarUrl) return;
        const token = getUnlockToken();
        const headers: Record<string, string> = {};
        if (token) headers["x-anima-unlock"] = token;
        const res = await fetch(`${API_BASE}${profile.avatarUrl}`, { headers });
        if (!res.ok || revoked) return;
        const blob = await res.blob();
        if (!revoked) setAvatarUrl(URL.createObjectURL(blob));
      })
      .catch(() => {});

    return () => {
      revoked = true;
    };
  }, [user?.id]);

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || user?.id == null) return;

    setUploading(true);
    setError("");
    try {
      await api.consciousness.uploadAgentAvatar(user.id, file);
      // Re-fetch to get the blob URL with auth
      const token = getUnlockToken();
      const headers: Record<string, string> = {};
      if (token) headers["x-anima-unlock"] = token;
      const res = await fetch(
        `${API_BASE}/consciousness/${user.id}/agent-profile/avatar`,
        { headers },
      );
      if (res.ok) {
        const blob = await res.blob();
        setAvatarUrl(URL.createObjectURL(blob));
      }
      // Notify Layout to refresh
      window.dispatchEvent(new CustomEvent("anima-avatar-changed"));
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
      setAvatarUrl(personaAvatar);
      window.dispatchEvent(new CustomEvent("anima-avatar-changed"));
    } catch (err: any) {
      setError(err.message || "Failed to remove avatar");
    }
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-xl mx-auto px-8 py-8 space-y-8">
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
              AVATAR AND IDENTITY
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
              className="w-20 h-20 rounded-full border border-border object-cover flex-shrink-0"
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
                disabled={uploading || avatarUrl === personaAvatar}
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
          <p className="font-mono text-[9px] text-muted-foreground/30 tracking-wider">
            Edit identity in the soul writer.
          </p>
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
