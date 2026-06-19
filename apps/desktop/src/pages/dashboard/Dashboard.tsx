import { useCallback, useEffect, useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import type {
  ChatContextMessage,
  Greeting,
  PresenceConfig,
  TaskItem,
  MemoryEpisodeData,
  EmotionalContextData,
  TodayContext,
} from "@anima/api-client";
import { api } from "../../lib/api";
import { PromptInput, DotLoader } from "@anima/standard-templates";
import { DashboardDiary } from "./DashboardDiary";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { TodayContextPanel } from "../../components/TodayContextPanel";
import {
  loadTodayContext,
  normalizeTodayContext,
  saveTodayContext,
  todayIso,
  type TodayContextDraft,
} from "../../lib/today-context";

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


const MOOD_CHAT_PROMPT: Record<string, string> = {
  calm:        "What's keeping you so grounded right now?",
  happy:       "What's bringing you that happiness?",
  excited:     "What's got you so excited?",
  curious:     "What are you most curious about today?",
  anxious:     "What's been weighing on your mind?",
  sad:         "Want to talk about how you're feeling?",
  angry:       "What's got you fired up?",
  frustrated:  "What's been frustrating you lately?",
  hopeful:     "What are you feeling hopeful about?",
  lonely:      "I'm here. What's making you feel distant?",
  content:     "What's bringing you that sense of peace?",
  tired:       "What's been draining your energy?",
  playful:     "What do you want to play with today?",
  worried:     "What's been weighing on you?",
  grateful:    "What are you feeling grateful for?",
  confused:    "What's got you feeling uncertain?",
  protective:  "What do you feel protective of?",
  affectionate:"Who or what are you feeling close to right now?",
};

function getMoodChatPrompt(emotion: string): string {
  return MOOD_CHAT_PROMPT[emotion.toLowerCase().trim()] ?? `Tell me more about feeling ${emotion}.`;
}

function relativeSession(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const h = Math.floor(diff / 3_600_000);
  const d = Math.floor(diff / 86_400_000);
  if (h < 1) return "just now";
  if (h < 24) return `${h}h ago`;
  if (d === 1) return "yesterday";
  if (d < 7) return `${d}d ago`;
  return `${Math.floor(d / 7)}w ago`;
}


function getMoodEmoji(emotion: string | null): string {
  if (!emotion) return "◌";
  return MOOD_EMOJI[emotion.toLowerCase().trim()] ?? "◌";
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
  const [todayContext, setTodayContext] = useState<TodayContext | null>(() =>
    loadTodayContext(),
  );

  const handleTodayContextSave = useCallback((draft: TodayContextDraft) => {
    const next = normalizeTodayContext(draft);
    setTodayContext(next);
    saveTodayContext(next);
  }, []);

  const handleTodayContextClear = useCallback(() => {
    setTodayContext(null);
    saveTodayContext(null);
  }, []);

  useEffect(() => {
    if (todayContext && todayContext.date !== todayIso()) {
      setTodayContext(null);
      saveTodayContext(null);
    }
  }, [todayContext]);

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
const lastSession = episodes[0]?.date ? relativeSession(episodes[0].date) : null;
  const todayContextLine =
    todayContext?.note ||
    [todayContext?.mood, todayContext?.energy].filter(Boolean).join(" · ") ||
    null;

  return (
    <div className="h-full overflow-y-auto">
      {/* ── Profile ── */}
      <div className="px-6 pt-16 relative z-10">
        <div className="max-w-2xl mx-auto">
          {/* Avatar row */}
          <div className="flex items-end gap-4 mb-4">
            <div className="w-40 h-48 border-2 border-background bg-card overflow-hidden shrink-0 shadow-xl">
              <img src={avatarUrl} alt={agentName} className="w-full h-full object-cover object-top" />
            </div>

            {/* Companion state panel */}
            <div
              className="flex-1 min-w-0 h-48 bg-card border border-border/50 shadow-md overflow-hidden self-end"
              style={{ display: "grid", gridTemplateRows: "auto 1px auto 1px 1fr" }}
            >
              {/* Name header */}
              <div className="flex items-baseline gap-2.5 px-4 py-2.5">
                <span className="text-base font-semibold tracking-tight text-foreground truncate">{agentName}</span>
                {relationship && (
                  <span className="font-mono text-[8px] tracking-[0.2em] uppercase text-muted-foreground/30 shrink-0">{relationship}</span>
                )}
              </div>

              {/* Divider */}
              <div className="bg-border/20 mx-4" />

              {/* Mode row */}
              <div className="flex items-center justify-between gap-3 px-4 py-2.5">
                {emotion ? (
                  <div className="flex items-center gap-2.5 min-w-0 overflow-hidden">
                    <span className="text-xl leading-none shrink-0">{getMoodEmoji(emotion)}</span>
                    <span className="text-sm font-semibold capitalize text-foreground/85 truncate">{emotion}</span>
                    {lastSession && (
                      <span className="font-mono text-[8px] text-muted-foreground/30 shrink-0">· {lastSession}</span>
                    )}
                  </div>
                ) : (
                  <span className="text-xs text-muted-foreground/30">—</span>
                )}
                {emotion && (
                  <button
                    onClick={() => handlePromptSubmit(getMoodChatPrompt(emotion))}
                    className="shrink-0 px-2 py-0.5 font-mono text-[9px] tracking-[0.15em] uppercase border border-border/50 text-muted-foreground/50 hover:border-accent/40 hover:text-accent transition-all"
                  >
                    ask
                  </button>
                )}
              </div>

              {/* Divider */}
              <div className="bg-border/25 mx-4" />

              {/* Monologue */}
              <div className="px-4 pt-3 pb-3.5 overflow-hidden flex flex-col justify-between">
                {briefLoading ? (
                  <DotLoader />
                ) : brief?.message ? (
                  <>
                    <div className="space-y-1.5 overflow-hidden">
                      <p className={`text-xs italic text-foreground/55 leading-relaxed animate-fade-in ${todayContextLine ? "line-clamp-2" : "line-clamp-3"}`}>
                        {brief.message}
                      </p>
                      {todayContextLine && (
                        <p className="font-mono text-[9px] tracking-[0.1em] text-muted-foreground/35 line-clamp-1">
                          {todayContextLine}
                        </p>
                      )}
                    </div>
                    <button
                      onClick={() => handlePromptSubmit("Let's talk about what's on your mind right now.")}
                      className="self-start font-mono text-[9px] tracking-[0.15em] uppercase text-muted-foreground/35 hover:text-foreground transition-colors"
                    >
                      explore →
                    </button>
                  </>
                ) : todayContextLine ? (
                  <div className="flex flex-col justify-between h-full">
                    <p className="text-xs text-foreground/45 leading-relaxed line-clamp-3 italic">
                      {todayContextLine}
                    </p>
                    <button
                      onClick={() => handlePromptSubmit("Let's talk about how I'm arriving today.")}
                      className="self-start font-mono text-[9px] tracking-[0.15em] uppercase text-muted-foreground/35 hover:text-foreground transition-colors"
                    >
                      explore →
                    </button>
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground/20 italic">...</p>
                )}
              </div>
            </div>
          </div>

          <div className="mt-3">
            <TodayContextPanel
              context={todayContext}
              greeting={todayContext ? null : "How are you arriving today?"}
              onSave={handleTodayContextSave}
              onClear={handleTodayContextClear}
            />
          </div>

          <div className="mt-3">
            <PromptInput agentName={agentName} onSubmit={handlePromptSubmit} size="lg" />
          </div>
        </div>
      </div>

      {/* ── Memory Grid ── */}
      <div className="px-6 pt-10 pb-0">
        <div className="max-w-2xl mx-auto">
          <div className="flex items-center gap-3 mb-3">
            <span className="font-mono text-[9px] tracking-[0.25em] uppercase text-muted-foreground/35">
              Memory Grid
            </span>
            <div className="flex-1 h-px bg-border/30" />
            <span className="font-mono text-[9px] tracking-[0.2em] uppercase text-muted-foreground/20">
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
                  className="bg-card border border-border/60 p-4 flex flex-col gap-2.5 hover:border-border/90 hover:shadow-sm transition-all cursor-default"
                >
                  <p className="text-sm font-semibold text-foreground/90 leading-snug line-clamp-2">
                    {title}
                  </p>
                  <p
                    className="text-xs leading-relaxed text-foreground/50 flex-1 overflow-hidden"
                    style={{
                      maskImage: "linear-gradient(to bottom, black 40%, transparent 100%)",
                      WebkitMaskImage: "linear-gradient(to bottom, black 40%, transparent 100%)",
                      maxHeight: "4.5rem",
                    }}
                  >
                    {ep.summary}
                  </p>
                  <div className="flex items-center gap-1.5 flex-wrap mt-auto pt-1 border-t border-border/20">
                    {ep.topics.slice(0, 3).map((t) => (
                      <span key={t} className="font-mono text-[8px] tracking-widest uppercase px-1.5 py-0.5 text-muted-foreground/50">
                        #{t}
                      </span>
                    ))}
                    {isTruncated && (
                      <button
                        onClick={() => setSelectedEpisode(ep)}
                        className="ml-auto font-mono text-[9px] tracking-wider text-accent/60 hover:text-accent transition-colors"
                      >
                        read →
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
      <div className="px-6 pt-10 pb-16">
        <div className="max-w-2xl mx-auto">
          <DashboardDiary episodes={episodes} agentName={agentName} avatarUrl={avatarUrl} onChat={handleEpisodeChat} />
          {episodes.length >= 3 && (
            <button
              onClick={() => navigate("/journal")}
              className="mt-5 w-full py-3 font-mono text-[9px] tracking-[0.25em] uppercase text-muted-foreground/40 hover:text-foreground/70 border border-border/40 hover:border-border/70 transition-all"
            >
              View all entries
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
