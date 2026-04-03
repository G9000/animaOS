import { useRef, useState } from "react";
import { useAuth } from "../../context/AuthContext";
import { useNavigate } from "react-router-dom";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { api } from "../../lib/api";
import { dispatchAgentProfileChanged } from "../../lib/events";

export default function AgentProfileSettings() {
  const { user } = useAuth();
  const navigate = useNavigate();

  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const { agentName, relationship, agentType, avatarUrl, hasCustomAvatar } =
    useAgentProfile(user?.id);

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
