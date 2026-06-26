import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import {
  ReactFlow,
  Background,
  useNodesState,
  type ReactFlowInstance,
  type NodeChange,
} from "@xyflow/react";
import { useAuth } from "../../context/AuthContext";
import type {
  ChatContextMessage,
  Greeting,
  Reflection,
  MessagePill,
  PresenceConfig,
  TaskItem,
  MemoryEpisodeData,
  EmotionalContextData,
  Nudge,
  Thread,
  DiaryEntryData,
} from "@anima/api-client";
import type { GalleryImage } from "./nodes/node-types";
import { api } from "../../lib/api";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { dashboardNodeTypes, type DashboardNode } from "./nodes";
import { buildInitialNodes } from "./layout";
import { useNodePositions } from "./useNodePositions";
import { AuthImage } from "../../components/AuthImage";

const GREETING_CACHE_KEY = "anima_dashboard_greeting";
const GREETING_CACHE_TTL_MS = 5 * 60 * 1000;
const CLOSED_NODES_KEY = "anima_dashboard_closed_nodes";

type CachedGreeting = { greeting: Greeting; ts: number; userId: number };

function clearCachedGreeting(): void {
  try {
    sessionStorage.removeItem(GREETING_CACHE_KEY);
  } catch {
    /* ignore */
  }
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
    )
      return parsed;
    clearCachedGreeting();
  } catch {
    /* ignore */
  }
  return null;
}

function setCachedGreeting(userId: number, greeting: Greeting): void {
  try {
    if (!greeting.llmGenerated) {
      clearCachedGreeting();
      return;
    }
    sessionStorage.setItem(
      GREETING_CACHE_KEY,
      JSON.stringify({ greeting, ts: Date.now(), userId }),
    );
  } catch {
    /* ignore */
  }
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

export default function Dashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { agentName, avatarUrl, relationship } = useAgentProfile(user?.id);

  const [brief, setBrief] = useState<Greeting | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [reflection, setReflection] = useState<Reflection | null>(null);
  const [reflectionLoading, setReflectionLoading] = useState(false);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [episodes, setEpisodes] = useState<MemoryEpisodeData[]>([]);
  const [mood, setMood] = useState<EmotionalContextData | null>(null);
  const [nudges, setNudges] = useState<Nudge[]>([]);
  const [galleryImages, setGalleryImages] = useState<GalleryImage[]>([]);
  const [recentThreads, setRecentThreads] = useState<Thread[]>([]);
  const [journalEntries, setJournalEntries] = useState<DiaryEntryData[]>([]);
  const [galleryLightbox, setGalleryLightbox] = useState<{
    images: GalleryImage[];
    index: number;
  } | null>(null);
  const [closedNodeIds, setClosedNodeIds] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(CLOSED_NODES_KEY);
      if (raw) return new Set(JSON.parse(raw));
    } catch {}
    return new Set<string>();
  });
  const [presenceConfig, setPresenceConfig] = useState<PresenceConfig | null>(
    null,
  );
  const [selectedEpisode, setSelectedEpisode] =
    useState<MemoryEpisodeData | null>(null);
  const [reactFlowInstance, setReactFlowInstance] =
    useState<ReactFlowInstance<DashboardNode> | null>(null);

  const [nodes, setNodes, onNodesChange] = useNodesState<DashboardNode>([]);

  useEffect(() => {
    if (user?.id == null) return;
    api.consciousness
      .getAgentProfile(user.id)
      .then((profile) => setNeedsSetup(!profile.setupComplete))
      .catch(() => setNeedsSetup(true));
  }, [user?.id]);

  useEffect(() => {
    if (user?.id == null) return;
    let active = true;
    api.presence
      .get(user.id)
      .then((c) => {
        if (active) setPresenceConfig(c);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [user?.id]);

  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;
    const cached = getCachedGreeting(user.id);
    if (cached) {
      setBrief(cached.greeting);
    } else {
      setBriefLoading(true);
      api.chat
        .greeting(user.id)
        .then((g) => {
          if (!active) return;
          setBrief(g);
          setCachedGreeting(user.id, g);
        })
        .catch(() => {
          if (!active) return;
          api.chat
            .brief(user.id)
            .then((b) => {
              if (!active) return;
              const fallback: Greeting = {
                message: b.message,
                llmGenerated: false,
                context: {
                  ...b.context,
                  overdueTasks: 0,
                  upcomingDeadlines: [],
                },
              };
              setBrief(fallback);
              setCachedGreeting(user.id, fallback);
            })
            .catch(() => {});
        })
        .finally(() => {
          if (active) setBriefLoading(false);
        });
    }
    return () => {
      active = false;
    };
  }, [user?.id, needsSetup]);

  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;
    setReflectionLoading(true);
    api.chat
      .reflection(user.id)
      .then((r) => {
        if (!active) return;
        setReflection(r);
      })
      .catch(() => {})
      .finally(() => {
        if (active) setReflectionLoading(false);
      });
    return () => {
      active = false;
    };
  }, [user?.id, needsSetup]);

  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;
    api.tasks
      .list(user.id)
      .then((list) => {
        if (!active) return;
        setTasks(list ?? []);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [user?.id, needsSetup]);

  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;
    api.memory
      .listEpisodes(user.id, 6)
      .then((data) => {
        if (!active) return;
        setEpisodes(data);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [user?.id, needsSetup]);

  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;
    api.consciousness
      .getEmotions(user.id, 5)
      .then((data) => {
        if (!active) return;
        setMood(data);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [user?.id, needsSetup]);

  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;
    api.chat
      .nudges(user.id)
      .then((data) => {
        if (!active) return;
        setNudges(data.nudges ?? []);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [user?.id, needsSetup]);

  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;
    void (async () => {
      const [diaryEntries, threadList] = await Promise.all([
        api.diary.list(user.id, 100).catch(() => []),
        api.threads.list().catch(() => ({ threads: [] })),
      ]);
      if (!active) return;

      // Populate threads immediately — don't wait for the gallery message fetches below
      setRecentThreads(threadList.threads);
      setJournalEntries(diaryEntries);

      const diaryImages: GalleryImage[] = diaryEntries
        .flatMap((e) => e.attachments)
        .filter((a) => a.kind === "image" || a.mimeType.startsWith("image/"))
        .map((a) => ({
          id: String(a.id),
          url: a.url,
          mimeType: a.mimeType,
          filename: a.filename,
          caption: a.caption,
          createdAt: a.createdAt,
          source: "diary" as const,
        }));

      // Fetch messages for the most recent threads (images only live in PG-backed threads)
      const threadMessages = await Promise.all(
        threadList.threads.slice(0, 15).map((t) =>
          api.threads
            .messages(t.id)
            .then((r) => r.messages)
            .catch(() => []),
        ),
      );
      if (!active) return;

      const chatImages: GalleryImage[] = threadMessages
        .flat()
        .filter((m) => m.role === "user" && (m.attachments?.length ?? 0) > 0)
        .flatMap((m) =>
          (m.attachments ?? [])
            .filter((a) => a.kind === "image")
            .map((a) => ({
              id: a.id,
              url: a.url,
              mimeType: a.mimeType,
              filename: a.filename ?? null,
              caption: null,
              createdAt: m.ts ?? null,
              source: "chat" as const,
            })),
        );

      const seen = new Set<string>();
      const merged = [...chatImages, ...diaryImages]
        .filter((img) => {
          if (seen.has(img.url)) return false;
          seen.add(img.url);
          return true;
        })
        .sort((a, b) => {
          if (!a.createdAt && !b.createdAt) return 0;
          if (!a.createdAt) return 1;
          if (!b.createdAt) return -1;
          return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime();
        });
      if (active) {
        setGalleryImages(merged);
      }
    })();
    return () => {
      active = false;
    };
  }, [user?.id, needsSetup]);

  // "chat" on a memory — open a fresh thread seeded with the episode summary as
  // the companion's opening message, then let the user reply. (No canned prompt.)
  const handleEpisodeChat = useCallback(
    (ep: MemoryEpisodeData) => {
      navigate("/chat", {
        state: {
          contextMessages: [
            {
              role: "assistant" as const,
              content: ep.summary,
              source: "episode_context",
            },
          ],
          seedThread: true,
        },
      });
    },
    [navigate],
  );

  const handleExploreMemory = useCallback(
    (episodeId: number) => {
      const ep = episodes.find((e) => e.id === episodeId);
      if (ep) {
        handleEpisodeChat(ep);
      } else {
        navigate("/chat", { state: { seedThread: true } });
      }
    },
    [episodes, handleEpisodeChat, navigate],
  );

  const handleToggleTask = useCallback(async (task: TaskItem) => {
    try {
      const updated = await api.tasks.update(task.id, { done: !task.done });
      setTasks((current) =>
        current.map((t) => (t.id === task.id ? updated : t)),
      );
    } catch {
      // ignore
    }
  }, []);

  const handleDeleteTask = useCallback(async (id: number) => {
    try {
      await api.tasks.delete(id);
      setTasks((current) => current.filter((t) => t.id !== id));
    } catch {
      // ignore
    }
  }, []);

  const handleAddTask = useCallback(
    async (text: string) => {
      if (user?.id == null) return;
      try {
        const created = await api.tasks.create(user.id, text);
        setTasks((current) => [created, ...current]);
      } catch {
        // ignore
      }
    },
    [user?.id],
  );

  const handleDismissNudge = useCallback((type: string) => {
    setNudges((current) => current.filter((n) => n.type !== type));
  }, []);

  const handleImageClick = useCallback(
    (images: GalleryImage[], index: number) => {
      setGalleryLightbox({ images, index });
    },
    [],
  );

  const handleCloseNode = useCallback(
    (id: string) => {
      setClosedNodeIds((current) => {
        if (current.has(id)) return current;
        const next = new Set(current);
        next.add(id);
        try {
          localStorage.setItem(CLOSED_NODES_KEY, JSON.stringify([...next]));
        } catch {}
        return next;
      });
      setNodes((current) => current.filter((n) => n.id !== id));
    },
    [setNodes],
  );

  const handleViewAllEntries = useCallback(() => {
    navigate("/journal");
  }, [navigate]);

  const handleOpenThread = useCallback(
    (threadId: number) => {
      navigate("/chat", { state: { resumeThreadId: threadId } });
    },
    [navigate],
  );

  const handleNewChat = useCallback(() => {
    navigate("/chat", { state: { seedThread: true } });
  }, [navigate]);

  const handleSaveCapture = useCallback(
    async (text: string) => {
      if (user?.id == null) return;
      const today = new Date().toISOString().split("T")[0];
      await api.diary.create(user.id, { entryDate: today, body: text });
    },
    [user?.id],
  );

  const handleNewEntry = useCallback(() => {
    navigate("/journal");
  }, [navigate]);

  // "ask →" / "start chat →" — open a fresh chat thread seeded with the
  // companion's current thought as the opening assistant message. Nothing is
  // sent; the thought rides along as context on the user's first reply.
  const handleExplore = useCallback(
    (thought: string, pills?: MessagePill[]) => {
      const trimmed = thought?.trim() ?? "";
      const canIncludeGreetingContext =
        presenceConfig?.enabled !== false &&
        presenceConfig?.homeGreetingContextEnabled !== false;
      const contextMessages: ChatContextMessage[] =
        trimmed && canIncludeGreetingContext
          ? [
              {
                role: "assistant",
                content: trimmed,
                source: "home_greeting",
                ...(pills && pills.length > 0 ? { pills } : {}),
              },
            ]
          : [];
      navigate("/chat", { state: { contextMessages, seedThread: true } });
    },
    [navigate, presenceConfig],
  );

  const initialNodes = useMemo(() => {
    if (user?.id == null) return [];
    return buildInitialNodes(
      {
        userName: user.name,
        agentName,
        avatarUrl,
        relationship,
        emotion: mood?.dominantEmotion ?? null,
        mood,
        lastSession: episodes[0]?.date ? relativeSession(episodes[0].date) : null,
        brief,
        briefLoading,
        reflection,
        reflectionLoading,
        tasks,
        currentFocus: brief?.context?.currentFocus ?? null,
        episodes,
        nudges,
        galleryImages,
        threads: recentThreads,
        journalEntries,
      },
      {
        onNavigate: navigate,
        onExplore: handleExplore,
        onToggleTask: handleToggleTask,
        onDeleteTask: handleDeleteTask,
        onAddTask: handleAddTask,
        onEpisodeChat: handleEpisodeChat,
        onEpisodeRead: setSelectedEpisode,
        onViewAllEntries: handleViewAllEntries,
        onDismissNudge: handleDismissNudge,
        onExploreMemory: handleExploreMemory,
        onCloseNode: handleCloseNode,
        onImageClick: handleImageClick,
        onOpenThread: handleOpenThread,
        onNewChat: handleNewChat,
        onSaveCapture: handleSaveCapture,
        onNewEntry: handleNewEntry,
      },
    ).filter((n) => !closedNodeIds.has(n.id));
  }, [
    agentName,
    avatarUrl,
    brief,
    briefLoading,
    reflection,
    reflectionLoading,
    episodes,
    galleryImages,
    handleAddTask,
    handleDeleteTask,
    handleEpisodeChat,
    handleExploreMemory,
    handleExplore,
    handleImageClick,
    handleToggleTask,
    handleViewAllEntries,
    handleDismissNudge,
    handleCloseNode,
    handleOpenThread,
    handleNewChat,
    handleSaveCapture,
    handleNewEntry,
    closedNodeIds,
    mood,
    navigate,
    nudges,
    relationship,
    recentThreads,
    journalEntries,
    tasks,
    user?.id,
    user?.name,
  ]);

  const { hydratedNodes, persistPositions } =
    useNodePositions(initialNodes);

  useEffect(() => {
    if (hydratedNodes) {
      setNodes(hydratedNodes);
    }
  }, [hydratedNodes, setNodes]);

  const handleResetDashboard = useCallback(() => {
    try {
      localStorage.removeItem("anima_dashboard_node_positions");
      localStorage.removeItem(CLOSED_NODES_KEY);
    } catch {}
    setClosedNodeIds(new Set<string>());
    if (hydratedNodes) {
      setNodes(hydratedNodes);
    }
    if (reactFlowInstance) {
      reactFlowInstance.fitView({ padding: 0.2, duration: 300 });
    }
  }, [hydratedNodes, reactFlowInstance, setNodes]);

  const handleNodeDragStop = useCallback(() => {
    persistPositions(nodes);
  }, [nodes, persistPositions]);

  // Intercept dimension changes from NodeResizer and persist after settling
  const resizeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleNodesChange = useCallback(
    (changes: NodeChange<DashboardNode>[]) => {
      onNodesChange(changes);
      if (changes.some((c) => c.type === "dimensions")) {
        if (resizeTimerRef.current) clearTimeout(resizeTimerRef.current);
        resizeTimerRef.current = setTimeout(() => {
          // nodes ref is stale here; read from setNodes callback
          setNodes((current) => {
            persistPositions(current);
            return current;
          });
        }, 300);
      }
    },
    [onNodesChange, persistPositions, setNodes],
  );

  const hasFittedView = useRef(false);
  useEffect(() => {
    if (reactFlowInstance && nodes.length > 0 && !hasFittedView.current) {
      hasFittedView.current = true;
      reactFlowInstance.fitView({ padding: 0.2, duration: 300 });
    }
  }, [reactFlowInstance, nodes.length]);

  if (needsSetup && user?.id != null) return <Navigate to="/init" replace />;
  if (needsSetup === null) return <div className="h-full" />;

  return (
    <div className="h-full w-full relative">
      <ReactFlow<DashboardNode>
        nodes={nodes}
        onNodesChange={handleNodesChange}
        nodeTypes={dashboardNodeTypes}
        edges={[]}
        onInit={setReactFlowInstance}
        onNodeDragStop={handleNodeDragStop}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.2}
        maxZoom={1.5}
        defaultViewport={{ x: 0, y: 0, zoom: 0.8 }}
        nodesDraggable
        nodesConnectable={false}
        elementsSelectable={false}
        connectOnClick={false}
        className="dashboard-flow bg-transparent"
      >
        <Background color="var(--border)" gap={24} size={1} />
      </ReactFlow>

      {/* ── Reset dashboard ── */}
      <button
        onClick={handleResetDashboard}
        className="absolute bottom-4 left-4 z-10 px-3 py-2 bg-card border border-border font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/55 hover:text-foreground hover:border-muted-foreground/30 transition-all"
        title="Reset dashboard layout"
      >
        Reset dashboard
      </button>

      {/* ── Gallery lightbox ── */}
      {galleryLightbox && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-background/85 backdrop-blur-sm"
          onClick={() => setGalleryLightbox(null)}
        >
          <div
            className="relative flex flex-col items-center gap-3 max-w-2xl w-full mx-6"
            onClick={(e) => e.stopPropagation()}
          >
            <AuthImage
              src={galleryLightbox.images[galleryLightbox.index].url}
              alt={
                galleryLightbox.images[galleryLightbox.index].caption ??
                galleryLightbox.images[galleryLightbox.index].filename ??
                ""
              }
              className="max-h-[75vh] max-w-full object-contain rounded shadow-2xl"
            />
            {galleryLightbox.images[galleryLightbox.index].caption && (
              <p className="font-mono text-[10px] tracking-wider text-muted-foreground/60 text-center">
                {galleryLightbox.images[galleryLightbox.index].caption}
              </p>
            )}
            <div className="flex items-center gap-6">
              <button
                onClick={() =>
                  setGalleryLightbox((lb) =>
                    lb
                      ? {
                          ...lb,
                          index:
                            (lb.index - 1 + lb.images.length) %
                            lb.images.length,
                        }
                      : null,
                  )
                }
                className="font-mono text-sm text-muted-foreground/50 hover:text-foreground transition-colors px-2"
                aria-label="Previous image"
              >
                ←
              </button>
              <span className="font-mono text-[9px] tracking-wider text-muted-foreground/40">
                {galleryLightbox.index + 1} / {galleryLightbox.images.length}
              </span>
              <button
                onClick={() =>
                  setGalleryLightbox((lb) =>
                    lb
                      ? {
                          ...lb,
                          index: (lb.index + 1) % lb.images.length,
                        }
                      : null,
                  )
                }
                className="font-mono text-sm text-muted-foreground/50 hover:text-foreground transition-colors px-2"
                aria-label="Next image"
              >
                →
              </button>
            </div>
          </div>
          <button
            onClick={() => setGalleryLightbox(null)}
            className="absolute top-4 right-4 w-8 h-8 flex items-center justify-center rounded-full bg-card/80 border border-border/50 text-muted-foreground/60 hover:text-foreground transition-colors text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>
      )}

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
                <img
                  src={avatarUrl}
                  alt={agentName}
                  className="w-full h-full object-cover"
                />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-semibold">{agentName}</div>
                <div className="font-mono text-[9px] tracking-wider text-muted-foreground/40 uppercase">
                  {new Date(selectedEpisode.date).toLocaleDateString(undefined, {
                    month: "long",
                    day: "numeric",
                    year: "numeric",
                  })}
                  {selectedEpisode.turnCount != null &&
                    selectedEpisode.turnCount > 0 &&
                    ` · ${selectedEpisode.turnCount} turns`}
                </div>
              </div>
              <div className="flex gap-0.5 shrink-0">
                {[1, 2, 3, 4, 5].map((n) => (
                  <span
                    key={n}
                    className={`text-[10px] leading-none ${
                      n <= selectedEpisode.significanceScore
                        ? "text-accent/70"
                        : "text-border"
                    }`}
                  >
                    ★
                  </span>
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
                    <span
                      key={t}
                      className="text-accent/60 text-xs font-mono tracking-wide hover:text-accent/80 transition-colors"
                    >
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
