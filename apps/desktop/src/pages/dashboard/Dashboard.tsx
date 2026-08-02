import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ThreadPreviewModal } from "./ThreadPreviewModal";
import { useNavigate, Navigate } from "react-router-dom";
import { useLayoutActions } from "../../context/LayoutActionsContext";
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
import { api, getUnlockToken } from "../../lib/api";
import {
  ambientConsentAllows,
  clearOneShotGreetings,
  getCachedGreeting,
  peekOneShotGreeting,
  setCachedGreeting,
  stashOneShotGreeting,
  takeOneShotGreeting,
  voiceableGreeting,
} from "../../lib/greetingCache";
import { buildMemoryImages } from "../../lib/image-memories";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { dashboardNodeTypes, type DashboardNode } from "./nodes";
import { buildInitialNodes } from "./layout";
import { useNodePositions } from "./useNodePositions";
import { AuthImage } from "../../components/AuthImage";

const CLOSED_NODES_KEY = "anima_dashboard_closed_nodes";

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
  const { registerDashboardReset, registerNudgeCount } = useLayoutActions();
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
  const [previewThreadId, setPreviewThreadId] = useState<number | null>(null);
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
    // A dream-bearing greeting stashed by an unmounted fetch (below) is
    // displayed at most once — and only if it is still BOTH consented and
    // unexpired. Consent: re-checked against the freshly loaded presence
    // config (PR #130 review), because an opt-out between the stash and
    // this mount must win. Expiry: the stash dies with the server-side
    // claim (PR #135 review), because past that deadline the same dream can
    // be offered through another channel and replaying this copy would
    // disclose it twice.
    // IL-015: acknowledge only once a dream-bearing greeting is actually
    // DISPLAYED — not when it is fetched or stashed. An unacknowledged claim
    // expires server-side and the dream is offered again, which is exactly
    // what should happen if the user never saw it. Best-effort: a failed ack
    // just means the dream returns later.
    // IL-015 (PR #135 review, P1): never voice a dream on the strength of
    // the device clock alone — confirm the claim against the row itself,
    // and show the dream-free copy on any uncertain answer.
    const voiceable = (g: Greeting) =>
      voiceableGreeting(g, (dreamId, claimToken) =>
        api.chat.confirmGreetingDreamClaim(user.id, dreamId, claimToken),
      );
    const ackIfDream = (g: Greeting) => {
      if (g.ambientDream && g.ambientDreamId != null && g.ambientDreamClaimToken) {
        void api.chat
          .ackGreetingDream(user.id, g.ambientDreamId, g.ambientDreamClaimToken)
          .catch(() => {});
      }
    };
    const fetchGreeting = () => {
      const cached = getCachedGreeting(user.id);
      if (cached) {
        setBrief(cached.greeting);
        return;
      }
      setBriefLoading(true);
      // Bind the handoff to the session that ASKED for this greeting
      // (PR #130 review): if the user logs out and someone else signs in
      // before this resolves, the late callback must not write A's
      // decrypted dream into B's storage.
      const originUnlockToken = getUnlockToken();
      api.chat
        .greeting(user.id)
        .then(async (g) => {
          if (!active) {
            // The dream inside is CLAIMED server-side — hand it to the next
            // mount instead of discarding it (PR #130 review). The stash
            // carries the claim's expiry and is dropped past it (IL-015).
            if (g.ambientDream) {
              stashOneShotGreeting(user.id, g, originUnlockToken);
            }
            return;
          }
          const shown = await voiceable(g);
          // Unmounted while confirming: the claim was renewed, so hand the
          // confirmed copy to the next mount rather than dropping it.
          if (!active) {
            if (shown.ambientDream) {
              stashOneShotGreeting(user.id, shown, originUnlockToken);
            }
            return;
          }
          setBrief(shown);
          ackIfDream(shown);
          setCachedGreeting(user.id, shown);
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
    };
    if (peekOneShotGreeting(user.id)) {
      void api.presence
        .get(user.id)
        .then((cfg) => {
          // Unmounted before the consent check resolved? LEAVE the queue
          // intact (PR #130 round 4) — this is the only copy of a claimed
          // dream, and the next mount can still render it.
          if (!active) return;
          setPresenceConfig(cfg);
          if (!ambientConsentAllows(cfg)) {
            // Consent withdrawn: the user asked for silence — discard.
            clearOneShotGreetings(user.id);
            return;
          }
          const oneShot = takeOneShotGreeting(user.id);
          if (oneShot) {
            void voiceable(oneShot).then((shown) => {
              if (!active) return;
              setBrief(shown);
              ackIfDream(shown);
            });
            return;
          }
          // The stash expired between the peek and the take (its claim went
          // stale): there is nothing safe to replay, so ask for a fresh
          // greeting. The dream was never acknowledged, so it is offerable
          // again and may simply come back in that response.
          fetchGreeting();
        })
        .catch(() => {
          // Unknown consent: prefer silence THIS mount, but keep the queue
          // for a mount that can actually verify consent.
        });
      return () => {
        active = false;
      };
    }
    fetchGreeting();
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

      // Fetch messages for the most recent threads (images only live in PG-backed threads)
      const threadGroups = await Promise.all(
        threadList.threads.slice(0, 15).map(async (thread) => ({
          thread,
          messages: await api.threads
            .messages(thread.id)
            .then((r) => r.messages)
            .catch(() => []),
        })),
      );
      if (!active) return;

      if (active) {
        setGalleryImages(buildMemoryImages({ diaryEntries, threadGroups }));
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

  const handlePreviewThread = useCallback((threadId: number) => {
    setPreviewThreadId(threadId);
  }, []);

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
      let source = thought ?? "";
      // IL-010 (PR #130 review): this handoff seeds chat context, which the
      // server places in the model history — so the ambient dream must not
      // ride along. When the text being explored IS the dream-bearing
      // greeting, swap in the server's dream-free copy.
      if (brief?.ambientDream && brief.handoffMessage && source === brief.message) {
        source = brief.handoffMessage;
      }
      const trimmed = source.trim();
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
    [navigate, presenceConfig, brief],
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
        onPreviewThread: handlePreviewThread,
        onOpenThread: handleOpenThread,
        onNewChat: handleNewChat,
        onSaveCapture: handleSaveCapture,
        onNewEntry: handleNewEntry,
        onNavigate: navigate,
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
    handlePreviewThread,
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

  useEffect(() => {
    registerDashboardReset(handleResetDashboard);
    return () => registerDashboardReset(null);
  }, [handleResetDashboard, registerDashboardReset]);

  useEffect(() => {
    registerNudgeCount(nudges.length);
    return () => registerNudgeCount(0);
  }, [nudges.length, registerNudgeCount]);


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

      {/* ── Thread preview modal ── */}
      {previewThreadId != null && (() => {
        const thread = recentThreads.find((t) => t.id === previewThreadId);
        if (!thread) return null;
        return (
          <ThreadPreviewModal
            thread={thread}
            onClose={() => setPreviewThreadId(null)}
            onOpenFull={() => {
              setPreviewThreadId(null);
              handleOpenThread(thread.id);
            }}
          />
        );
      })()}
    </div>
  );
}
