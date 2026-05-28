import { useEffect, useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import type {
  ChatContextMessage,
  Greeting,
  PresenceConfig,
  TaskItem,
  MemoryEpisodeData,
  EmotionalContextData,
} from "@anima/api-client";
import { api } from "../../lib/api";
import { PromptInput, DotLoader, cn } from "@anima/standard-templates";
import { DashboardDiary } from "./DashboardDiary";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import banner1 from "../../assets/banner_1.jpg";
import banner2 from "../../assets/banner_2.jpg";

const BANNERS = [banner1, banner2];

const GREETING_CACHE_KEY = "anima_dashboard_greeting";
const GREETING_CACHE_TTL_MS = 5 * 60 * 1000;

type CachedGreeting = { greeting: Greeting; ts: number; userId: number };

function clearCachedGreeting(): void {
  try { sessionStorage.removeItem(GREETING_CACHE_KEY); } catch { /* ignore */ }
}

function getCachedGreeting(userId: number): CachedGreeting | null {
  try {
    const raw = sessionStorage.getItem(GREETING_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      parsed?.userId === userId &&
      parsed?.greeting?.llmGenerated === true &&
      typeof parsed.ts === "number" &&
      Date.now() - parsed.ts < GREETING_CACHE_TTL_MS
    ) return parsed;
    clearCachedGreeting();
  } catch { /* ignore */ }
  return null;
}

function setCachedGreeting(userId: number, greeting: Greeting): void {
  try {
    if (!greeting.llmGenerated) { clearCachedGreeting(); return; }
    sessionStorage.setItem(GREETING_CACHE_KEY, JSON.stringify({ greeting, ts: Date.now(), userId }));
  } catch { /* ignore */ }
}


const MOOD_EMOJI: Record<string, string> = {
  calm: "🌿", happy: "✨", excited: "⚡", curious: "🔍", anxious: "🌊",
  sad: "🌧", angry: "🔥", frustrated: "💢", hopeful: "🌅", lonely: "🌑",
  content: "☕", tired: "🌫", playful: "🎈", worried: "🌩", grateful: "🙏",
  confused: "🌀", protective: "🛡", affectionate: "💗",
};

const POSITIVE_MOODS = new Set(["happy", "excited", "hopeful", "grateful", "content", "playful", "affectionate", "calm"]);
const NEGATIVE_MOODS = new Set(["sad", "lonely", "tired", "angry", "frustrated", "anxious", "worried"]);

function getMoodEmoji(emotion: string | null): string {
  if (!emotion) return "◌";
  return MOOD_EMOJI[emotion.toLowerCase().trim()] ?? "◌";
}

function moodBadgeClass(emotion: string): string {
  const e = emotion.toLowerCase().trim();
  if (POSITIVE_MOODS.has(e)) return "bg-accent/15 text-accent border border-accent/25";
  if (NEGATIVE_MOODS.has(e)) return "bg-destructive/10 text-destructive border border-destructive/20";
  return "bg-border/50 text-muted-foreground border border-border";
}

export default function Dashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { agentName, avatarUrl, relationship } = useAgentProfile(user?.id);

  const [brief, setBrief] = useState<Greeting | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [_tasks, setTasks] = useState<TaskItem[]>([]);
  const [episodes, setEpisodes] = useState<MemoryEpisodeData[]>([]);
  const [mood, setMood] = useState<EmotionalContextData | null>(null);
  const [presenceConfig, setPresenceConfig] = useState<PresenceConfig | null>(null);
  const [selectedEpisode, setSelectedEpisode] = useState<MemoryEpisodeData | null>(null);

  useEffect(() => {
    if (user?.id == null) return;
    api.consciousness.getAgentProfile(user.id)
      .then((profile) => setNeedsSetup(!profile.setupComplete))
      .catch(() => setNeedsSetup(true));
  }, [user?.id]);

  useEffect(() => {
    if (user?.id == null) return;
    let active = true;
    api.presence.get(user.id).then((c) => { if (active) setPresenceConfig(c); }).catch(() => {});
    return () => { active = false; };
  }, [user?.id]);

  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;
    const cached = getCachedGreeting(user.id);
    if (cached) {
      setBrief(cached.greeting);
    } else {
      setBriefLoading(true);
      api.chat.greeting(user.id)
        .then((g) => { if (!active) return; setBrief(g); setCachedGreeting(user.id, g); })
        .catch(() => {
          if (!active) return;
          api.chat.brief(user.id)
            .then((b) => {
              if (!active) return;
              const fallback: Greeting = { message: b.message, llmGenerated: false, context: { ...b.context, overdueTasks: 0, upcomingDeadlines: [] } };
              setBrief(fallback);
              setCachedGreeting(user.id, fallback);
            })
            .catch(() => {});
        })
        .finally(() => { if (active) setBriefLoading(false); });
    }
    return () => { active = false; };
  }, [user?.id, needsSetup]);

  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;
    api.tasks.list(user.id)
      .then((list) => { if (!active) return; setTasks((list ?? []).filter((t) => !t.done).slice(0, 5)); })
      .catch(() => {});
    return () => { active = false; };
  }, [user?.id, needsSetup]);

  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;
    api.memory.listEpisodes(user.id, 3)
      .then((data) => { if (!active) return; setEpisodes(data); })
      .catch(() => {});
    return () => { active = false; };
  }, [user?.id, needsSetup]);

  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;
    api.consciousness.getEmotions(user.id, 5)
      .then((data) => { if (!active) return; setMood(data); })
      .catch(() => {});
    return () => { active = false; };
  }, [user?.id, needsSetup]);

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

  const handlePromptSubmit = (value: string) => {
    const firstName = user?.name?.split(" ")[0];
    const fallbackGreeting = `Hi${firstName ? ` ${firstName}` : ""}, how can I help you today?`;
    const currentGreeting = briefLoading ? null : brief?.message ?? fallbackGreeting;
    const canIncludeGreetingContext =
      presenceConfig?.enabled !== false &&
      presenceConfig?.homeGreetingContextEnabled !== false;
    const contextMessages: ChatContextMessage[] = currentGreeting && canIncludeGreetingContext
      ? [{ role: "assistant", content: currentGreeting, source: "home_greeting" }]
      : [];
    navigate(`/chat?msg=${encodeURIComponent(value)}`, { state: { contextMessages } });
  };

  if (needsSetup && user?.id != null) return <Navigate to="/init" replace />;
  if (needsSetup === null) return <div className="h-full" />;

  const emotion = mood?.dominantEmotion ?? null;

  return (
    <div className="h-full overflow-y-auto">

      {/* ── Banner ── */}
      <div className="relative h-48 w-full overflow-hidden bg-card">
        <img
          src={BANNERS[0]}
          alt=""
          className="absolute inset-0 w-full h-full object-cover object-top"
        />
      </div>

      {/* ── Profile ── */}
      <div className="px-6 relative z-10">
        <div className="max-w-2xl mx-auto">
          {/* Avatar row — only avatar overlaps the banner */}
          <div className="flex items-end justify-between -mt-10 mb-3">
            <div className="w-20 h-20 rounded-full border-4 border-background bg-card overflow-hidden shrink-0 shadow-md">
              <img src={avatarUrl} alt={agentName} className="w-full h-full object-cover" />
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-base font-semibold">{agentName}</span>
            {relationship && (
              <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/40">
                {relationship}
              </span>
            )}
            {emotion && (
              <span className={cn("font-mono text-[8px] px-1.5 py-0.5 tracking-wider uppercase", moodBadgeClass(emotion))}>
                {getMoodEmoji(emotion)} {emotion}
              </span>
            )}
          </div>

          <div className="mt-2">
            {briefLoading ? (
              <DotLoader />
            ) : brief?.message ? (
              <p className="text-base italic text-foreground/75 leading-relaxed animate-fade-in">
                "{brief.message}"
              </p>
            ) : null}
          </div>

          <div className="mt-3">
            <PromptInput agentName={agentName} onSubmit={handlePromptSubmit} size="lg" />
          </div>
        </div>
      </div>

      {/* ── Memory Grid ── */}
      <div className="px-6 pt-8 pb-0">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-center gap-3 mb-2">
            <span className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/40">
              Memory Grid
            </span>
            <div className="flex-1 h-px bg-border/40" />
            <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/25">
              {episodes.length} episodes
            </span>
          </div>
          <div className="grid grid-cols-2 gap-2">
            {episodes.slice(0, 6).map((ep) => {
              const isTruncated = ep.summary.length > 120;
              const title = ep.topics[0]
                ? ep.topics[0].replace(/-/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
                : ep.summary.slice(0, 40);
              return (
                <div
                  key={ep.id}
                  className="bg-card border border-border/70 p-4 flex flex-col gap-2.5 hover:border-border transition-colors cursor-default"
                >
                  <p className="text-sm font-semibold text-foreground leading-snug line-clamp-2">
                    {title}
                  </p>
                  <p
                    className="text-sm leading-relaxed text-foreground/65 flex-1 overflow-hidden"
                    style={{
                      maskImage: "linear-gradient(to bottom, black 40%, transparent 100%)",
                      WebkitMaskImage: "linear-gradient(to bottom, black 40%, transparent 100%)",
                      maxHeight: "4.5rem",
                    }}
                  >
                    {ep.summary}
                  </p>
                  <div className="flex items-center gap-1.5 flex-wrap mt-auto pt-0.5">
                    {ep.topics.slice(0, 3).map((t) => (
                      <span key={t} className="font-mono text-[9px] tracking-widest uppercase px-2 py-0.5 bg-secondary border border-border text-foreground/70">
                        {t}
                      </span>
                    ))}
                    {isTruncated && (
                      <button
                        onClick={() => setSelectedEpisode(ep)}
                        className="ml-auto font-mono text-[10px] tracking-wider text-accent/70 hover:text-accent transition-colors"
                      >
                        read more →
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      {/* ── Feed ── */}
      <div className="px-6 pt-8 pb-12">
        <div className="max-w-2xl mx-auto">
          <DashboardDiary episodes={episodes} agentName={agentName} avatarUrl={avatarUrl} onChat={handleEpisodeChat} />
          {episodes.length >= 3 && (
            <button
              onClick={() => navigate("/journal")}
              className="mt-4 w-full py-2.5 font-mono text-[10px] tracking-[0.22em] uppercase text-muted-foreground/50 hover:text-foreground border border-border/50 hover:border-border transition-all"
            >
              View all entries →
            </button>
          )}
        </div>
      </div>

      {/* ── Episode detail modal ── */}
      {selectedEpisode && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-background/75 backdrop-blur-sm"
          onClick={() => setSelectedEpisode(null)}
        >
          <div
            className="relative bg-card border border-border w-full max-w-md mx-6 shadow-xl animate-fade-in"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 p-4 border-b border-border/60">
              <div className="w-8 h-8 rounded-full overflow-hidden bg-background border border-border/60 shrink-0">
                <img src={avatarUrl} alt={agentName} className="w-full h-full object-cover" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-semibold">{agentName}</div>
                <div className="font-mono text-[9px] tracking-wider text-muted-foreground/40 uppercase">
                  {new Date(selectedEpisode.date).toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" })}
                  {selectedEpisode.turnCount != null && selectedEpisode.turnCount > 0 && ` · ${selectedEpisode.turnCount} turns`}
                </div>
              </div>
              <div className="flex gap-0.5 shrink-0">
                {[1, 2, 3, 4, 5].map((n) => (
                  <span key={n} className={`text-[10px] leading-none ${n <= selectedEpisode.significanceScore ? "text-accent/70" : "text-border"}`}>★</span>
                ))}
              </div>
              <button
                onClick={() => setSelectedEpisode(null)}
                className="shrink-0 ml-1 text-muted-foreground/40 hover:text-foreground transition-colors text-lg leading-none"
              >
                ×
              </button>
            </div>
            <div className="p-4 space-y-3">
              {selectedEpisode.emotionalArc && (
                <p className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/40">
                  {selectedEpisode.emotionalArc}
                </p>
              )}
              <p className="text-sm text-foreground/90 leading-relaxed">
                {selectedEpisode.summary}
              </p>
              {selectedEpisode.topics.length > 0 && (
                <div className="flex gap-2 flex-wrap pt-1">
                  {selectedEpisode.topics.map((t) => (
                    <span key={t} className="text-accent/60 text-xs font-mono tracking-wide hover:text-accent/80 transition-colors">
                      #{t}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
