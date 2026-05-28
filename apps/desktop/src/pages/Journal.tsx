import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import type { MemoryEpisodeData } from "@anima/api-client";
import { api } from "../lib/api";
import { useAgentProfile } from "../hooks/useAgentProfile";
import { DashboardDiary } from "./dashboard/DashboardDiary";

export default function Journal() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { agentName, avatarUrl } = useAgentProfile(user?.id);
  const [episodes, setEpisodes] = useState<MemoryEpisodeData[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (user?.id == null) return;
    api.memory
      .listEpisodes(user.id, 50)
      .then((data) => setEpisodes(data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [user?.id]);

  const handleEpisodeChat = (ep: MemoryEpisodeData) => {
    const topic = ep.topics[0]
      ? ep.topics[0].replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
      : null;
    const msg = topic ? `Let's talk about ${topic}` : "Let's revisit this memory";
    navigate(`/chat?msg=${encodeURIComponent(msg)}`, {
      state: {
        contextMessages: [{
          role: "assistant" as const,
          content: ep.summary,
          source: "episode_context",
        }],
      },
    });
  };

  return (
    <div className="h-full overflow-y-auto pt-16">
      <div className="max-w-2xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-xl font-semibold text-foreground">{agentName}&apos;s Journal</h1>
          <p className="font-mono text-[10px] tracking-[0.2em] uppercase text-muted-foreground/50 mt-1">
            {episodes.length} {episodes.length === 1 ? "entry" : "entries"}
          </p>
        </div>

        {loading ? (
          <div className="flex items-center gap-1.5 py-12 justify-center">
            <span className="w-1 h-1 bg-muted-foreground/40 animate-pulse" />
            <span className="w-1 h-1 bg-muted-foreground/40 animate-pulse [animation-delay:150ms]" />
            <span className="w-1 h-1 bg-muted-foreground/40 animate-pulse [animation-delay:300ms]" />
          </div>
        ) : (
          <DashboardDiary
            episodes={episodes}
            agentName={agentName}
            avatarUrl={avatarUrl}
            onChat={handleEpisodeChat}
            hideHeader
          />
        )}
      </div>
    </div>
  );
}
