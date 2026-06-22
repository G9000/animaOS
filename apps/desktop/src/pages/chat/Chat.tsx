import { useState, useEffect, useRef, useCallback } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import type {
  ChatAttachment,
  ChatContextMessage,
  ChatMessage,
  ChatRequestAttachment,
  ProactiveNotice,
  TodayContext,
  Thread,
  TraceEvent,
} from "@anima/api-client";
import { api } from "../../lib/api";
import { API_BASE, API_ORIGIN } from "../../lib/runtime";
import { getUnlockToken } from "../../lib/api";
import {
  loadTodayContext,
  normalizeTodayContext,
  saveTodayContext,
  suggestTodayContextFromMessage,
  todayIso,
  type TodayContextDraft,
} from "../../lib/today-context";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";
import personaAvatar from "../../assets/persona-default.svg";

// Chat components from standard-templates
import {
  ChatBubble,
  CompactChatBubble,
  shouldGroupMessages,
} from "@anima/standard-templates";
import {
  getTranslateLang,
  getShowTrace,
  setShowTrace as persistShowTrace,
} from "../../lib/preferences";

// Local chat components
import {
  ThreadSidebar,
  StreamingView,
  ChatEmptyState,
  ChatLayout,
} from "../../components/chat";
import { TodayContextPanel } from "../../components/TodayContextPanel";

// Toggle between bubble styles
const USE_COMPACT_BUBBLE = true;
const ACCEPTED_IMAGE_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
]);
const MAX_SELECTED_IMAGES = 4;

interface ChatLocationState {
  contextMessages?: ChatContextMessage[];
  // When true, open a fresh thread and show contextMessages as the opening
  // assistant message(s) without auto-sending a user prompt.
  seedThread?: boolean;
}

interface PendingImageAttachment {
  id: string;
  file: File;
  previewUrl: string;
}

function attachmentFetchUrl(url: string): string {
  if (url.startsWith("blob:") || url.startsWith("data:")) return url;
  if (url.startsWith("/api/")) return `${API_ORIGIN}${url}`;
  if (url.startsWith("/")) return `${API_BASE}${url}`;
  return url;
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === "string" ? reader.result : "";
      resolve(result.includes(",") ? result.split(",", 2)[1] : result);
    };
    reader.onerror = () => reject(reader.error ?? new Error("Read failed"));
    reader.readAsDataURL(file);
  });
}

async function toRequestAttachment(
  image: PendingImageAttachment,
): Promise<ChatRequestAttachment> {
  return {
    kind: "image",
    filename: image.file.name,
    mimeType: image.file.type,
    data: await readFileAsBase64(image.file),
  };
}

function toPreviewAttachment(image: PendingImageAttachment): ChatAttachment {
  return {
    id: image.id,
    kind: "image",
    mimeType: image.file.type,
    filename: image.file.name,
    sizeBytes: image.file.size,
    url: image.previewUrl,
  };
}

function MessagePills({ pills }: { pills?: ChatMessage["pills"] }) {
  if (!pills || pills.length === 0) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-1 not-prose">
      {pills.map((pill) => (
        <span
          key={`${pill.kind}:${pill.label}`}
          className="font-mono text-[8px] tracking-[0.15em] uppercase text-muted-foreground/55 border border-border/60 px-1.5 py-0.5"
        >
          {pill.label}
        </span>
      ))}
    </div>
  );
}

function ChatImageAttachments({
  attachments,
}: {
  attachments?: ChatAttachment[];
}) {
  if (!attachments || attachments.length === 0) return null;
  return (
    <div className="mb-2 grid grid-cols-2 gap-2 max-w-sm">
      {attachments.map((attachment) => (
        <AttachmentImage key={attachment.id} attachment={attachment} />
      ))}
    </div>
  );
}

function AttachmentImage({ attachment }: { attachment: ChatAttachment }) {
  const [src, setSrc] = useState(
    attachment.url.startsWith("blob:") || attachment.url.startsWith("data:")
      ? attachment.url
      : "",
  );
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    if (attachment.url.startsWith("blob:") || attachment.url.startsWith("data:")) {
      setSrc(attachment.url);
      return;
    }

    let revokedUrl: string | null = null;
    let cancelled = false;
    const token = getUnlockToken();
    fetch(attachmentFetchUrl(attachment.url), {
      headers: token ? { "x-anima-unlock": token } : {},
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("missing");
        const objectUrl = URL.createObjectURL(await response.blob());
        if (cancelled) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        revokedUrl = objectUrl;
        setSrc(objectUrl);
      })
      .catch(() => {
        if (!cancelled) setMissing(true);
      });

    return () => {
      cancelled = true;
      if (revokedUrl) URL.revokeObjectURL(revokedUrl);
    };
  }, [attachment.url]);

  if (missing || !src) {
    return (
      <div className="aspect-video border border-primary-foreground/20 bg-primary-foreground/10 font-mono text-[9px] tracking-wider text-primary-foreground/60 flex items-center justify-center">
        IMAGE MISSING
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={attachment.filename || "Attached image"}
      className="aspect-video w-full object-cover border border-primary-foreground/20 bg-primary-foreground/10"
    />
  );
}

function SelectedImagePreviews({
  images,
  onRemove,
}: {
  images: PendingImageAttachment[];
  onRemove: (id: string) => void;
}) {
  if (images.length === 0) return null;
  return (
    <div className="mb-2 grid grid-cols-4 gap-2">
      {images.map((image) => (
        <div key={image.id} className="relative border border-border bg-card">
          <img
            src={image.previewUrl}
            alt={image.file.name}
            className="h-16 w-full object-cover"
          />
          <button
            type="button"
            onClick={() => onRemove(image.id)}
            className="absolute right-1 top-1 h-5 w-5 bg-background/90 border border-border font-mono text-[10px] text-muted-foreground hover:text-foreground"
            title="Remove image"
          >
            x
          </button>
        </div>
      ))}
    </div>
  );
}

// Thread utilities
function sortThreads(threads: Thread[]): Thread[] {
  return [...threads].sort((left, right) => {
    const leftTime = new Date(
      left.lastMessageAt ?? left.createdAt ?? 0,
    ).getTime();
    const rightTime = new Date(
      right.lastMessageAt ?? right.createdAt ?? 0,
    ).getTime();
    return rightTime - leftTime;
  });
}

function dedupeThreads(threads: Thread[]): Thread[] {
  const unique = new Map<number, Thread>();
  for (const thread of threads) {
    const existing = unique.get(thread.id);
    unique.set(thread.id, existing ? { ...existing, ...thread } : thread);
  }
  return sortThreads(Array.from(unique.values()));
}

function mapThreadMessages(
  messages: Array<{
    role: string;
    content: string;
    ts?: string | null;
    retrieval?: ChatMessage["retrieval"];
    attachments?: ChatAttachment[];
    pills?: ChatMessage["pills"];
  }>,
  userId: number,
): ChatMessage[] {
  return messages
    .filter((message) => message.role === "user" || message.role === "assistant")
    .map((message, index) => ({
      id: index,
      userId,
      role: message.role as "user" | "assistant",
      content: message.content,
      createdAt: message.ts ?? undefined,
      retrieval: message.retrieval ?? undefined,
      attachments: message.attachments ?? [],
      pills: message.pills ?? undefined,
    }));
}

// Render context messages (the companion's opening thought/notice) as assistant
// bubbles that seed a fresh thread before the user has replied.
function contextToSeedMessages(
  contextMessages: ChatContextMessage[],
  userId: number,
): ChatMessage[] {
  return contextMessages
    .filter((message) => message.content.trim())
    .map((message, index) => ({
      id: Date.now() + index,
      userId,
      role: "assistant" as const,
      content: message.content.trim(),
      source: message.source ?? null,
      pills: message.pills ?? undefined,
    }));
}

// Translate handler
async function translateText(text: string, lang: string): Promise<string> {
  return await api.translate(text, lang);
}

export default function Chat() {
  const { user } = useAuth();
  const location = useLocation();
  const [searchParams, setSearchParams] = useSearchParams();
  const locationState = location.state as ChatLocationState | null;
  const pendingMsgRef = useRef<string | null>(searchParams.get("msg"));
  const pendingContextRef = useRef<ChatContextMessage[]>(
    locationState?.contextMessages ?? [],
  );
  // True while showing an unsent seeded thread (opened via the dashboard
  // "ask"/"start chat" actions). Cleared once the user sends or switches threads.
  const seedActiveRef = useRef(locationState?.seedThread === true);

  // Messages & input
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [selectedImages, setSelectedImages] = useState<PendingImageAttachment[]>(
    [],
  );
  const [todayContext, setTodayContext] = useState<TodayContext | null>(() =>
    loadTodayContext(),
  );
  const [todayGreeting, setTodayGreeting] = useState<string | null>(null);
  const [todaySuggestion, setTodaySuggestion] = useState<TodayContext | null>(
    null,
  );
  const [error, setError] = useState("");
  const [translateLang] = useState(getTranslateLang());
  const fileInputRef = useRef<HTMLInputElement>(null);
  const objectUrlsRef = useRef<Set<string>>(new Set());

  // Streaming state
  const [streaming, setStreaming] = useState(false);
  const [streamBuffer, setStreamBuffer] = useState("");
  const [reasoningBuffer, setReasoningBuffer] = useState("");
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [showTrace, setShowTrace] = useState(() => getShowTrace());

  // Thread state
  const [threads, setThreads] = useState<Thread[]>([]);
  const [currentThreadId, setCurrentThreadId] = useState<number | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [threadSearch, setThreadSearch] = useState("");
  const currentThreadIdRef = useRef<number | null>(null);

  // Avatar
  const [agentAvatarUrl, setAgentAvatarUrl] = useState<string>(personaAvatar);

  const handleTodayContextSave = useCallback((draft: TodayContextDraft) => {
    const next = normalizeTodayContext(draft);
    setTodayContext(next);
    saveTodayContext(next);
    setTodaySuggestion(null);
  }, []);

  const handleTodayContextClear = useCallback(() => {
    setTodayContext(null);
    saveTodayContext(null);
  }, []);

  const handleTodaySuggestionAccept = useCallback(() => {
    const next =
      todaySuggestion?.date === todayIso()
        ? normalizeTodayContext(todaySuggestion, todaySuggestion.date)
        : null;
    setTodayContext(next);
    saveTodayContext(next);
    setTodaySuggestion(null);
  }, [todaySuggestion]);

  const handleTodaySuggestionDismiss = useCallback(() => {
    setTodaySuggestion(null);
  }, []);

  // Scroll state
  const [isAtBottom, setIsAtBottom] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const historyHydratedRef = useRef(false);

  useEffect(() => {
    return () => {
      for (const url of objectUrlsRef.current) {
        URL.revokeObjectURL(url);
      }
      objectUrlsRef.current.clear();
    };
  }, []);

  const revokeImagePreviews = useCallback((images: PendingImageAttachment[]) => {
    for (const image of images) {
      URL.revokeObjectURL(image.previewUrl);
      objectUrlsRef.current.delete(image.previewUrl);
    }
  }, []);

  // Fetch the companion's proactive opener (if any). The caller decides how to
  // present it — currently as the opening bubble of a fresh thread.
  const loadProactiveNoticeData = useCallback(
    async (): Promise<ProactiveNotice | null> => {
      if (user?.id == null) return null;
      try {
        const result = await api.chat.proactiveNotice(user.id);
        return result.notice;
      } catch {
        return null;
      }
    },
    [user?.id],
  );

  useEffect(() => {
    setTodayGreeting(null);
    setTodaySuggestion(null);
  }, [user?.id]);

  useEffect(() => {
    if (user?.id == null || todayContext !== null || todayGreeting !== null) {
      return;
    }
    let active = true;
    api.chat
      .greeting(user.id)
      .then((greeting) => {
        const message = greeting.message.trim();
        if (active && message) setTodayGreeting(message);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, [todayContext, todayGreeting, user?.id]);

  // ===== Initial data loading =====
  // One coherent landing flow. The chat always shows a single thread:
  //   1. seeded (opened from the dashboard) → fresh thread, companion's
  //      thought/episode as the opening bubble;
  //   2. pending msg (?msg=) → load the active thread, then auto-send;
  //   3. existing active thread with messages → resume it;
  //   4. otherwise a fresh start → let the companion open with a proactive
  //      message as the first bubble, if it has one.
  useEffect(() => {
    if (user?.id == null) return;
    const userId = user.id;
    let revoked = false;

    // Avatar — independent of the conversation flow.
    api.consciousness
      .getAgentProfile(userId)
      .then(async (profile) => {
        if (!profile.avatarUrl || revoked) return;
        const token = getUnlockToken();
        const headers: Record<string, string> = token
          ? { "x-anima-unlock": token }
          : {};
        const res = await fetch(`${API_BASE}${profile.avatarUrl}`, { headers });
        if (res.ok && !revoked) {
          setAgentAvatarUrl(URL.createObjectURL(await res.blob()));
        }
      })
      .catch(() => {});

    const seedFreshThread = (context: ChatContextMessage[]) => {
      currentThreadIdRef.current = null;
      setCurrentThreadId(null);
      setMessages(contextToSeedMessages(context, userId));
    };

    void (async () => {
      // /history returns the active thread's messages, so it and the thread
      // list describe the same thread — load them together.
      const [threadsRes, hist] = await Promise.all([
        api.threads.list().catch(() => null),
        api.chat.history(userId).catch(() => null),
      ]);
      if (revoked) return;

      const nextThreads = threadsRes ? dedupeThreads(threadsRes.threads) : [];
      setThreads(nextThreads);
      const active = nextThreads.find((t) => t.status === "active") ?? null;

      // 1. Seeded open from the dashboard.
      if (seedActiveRef.current) {
        seedFreshThread(pendingContextRef.current);
        return;
      }

      // 2. Pending auto-send (e.g. /chat?msg=...).
      const pending = pendingMsgRef.current;
      if (pending) {
        setMessages(hist ?? []);
        if (active) {
          setCurrentThreadId(active.id);
          currentThreadIdRef.current = active.id;
        }
        const pendingContext = pendingContextRef.current;
        pendingMsgRef.current = null;
        pendingContextRef.current = [];
        setSearchParams({}, { replace: true });
        setTimeout(() => sendMessage(pending, pendingContext), 100);
        return;
      }

      // 3. Resume an existing conversation.
      if (active && (hist?.length ?? 0) > 0) {
        setCurrentThreadId(active.id);
        currentThreadIdRef.current = active.id;
        setMessages(hist ?? []);
        return;
      }

      // 4. Fresh start — let the companion open with a proactive message.
      setMessages([]);
      const notice = await loadProactiveNoticeData();
      if (revoked || seedActiveRef.current) return;
      const opener = (notice?.contextMessages ?? []).filter((m) =>
        m.content.trim(),
      );
      if (opener.length === 0) return;
      seedActiveRef.current = true;
      pendingContextRef.current = opener;
      seedFreshThread(opener);
    })();

    return () => {
      revoked = true;
    };
  }, [loadProactiveNoticeData, user?.id]);

  // ===== CONSOLIDATED: Polling for updates =====
  useEffect(() => {
    if (user?.id == null) return;

    const interval = setInterval(async () => {
      if (streaming || currentThreadIdRef.current != null || seedActiveRef.current)
        return;
      try {
        const hist = await api.chat.history(user.id);
        setMessages((prev) => (hist.length > prev.length ? hist : prev));
      } catch {}
    }, 10_000);

    return () => clearInterval(interval);
  }, [user?.id, streaming]);

  // ===== CONSOLIDATED: Auto-scroll =====
  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    bottomRef.current?.scrollIntoView({ behavior, block: "end" });
  }, []);

  useEffect(() => {
    if (!historyHydratedRef.current && messages.length > 0) {
      scrollToBottom("auto");
      historyHydratedRef.current = true;
    }
    if (streaming || isAtBottom) {
      scrollToBottom(streaming ? "auto" : "smooth");
    }
  }, [messages, streamBuffer, streaming, isAtBottom, scrollToBottom]);

  const updateScrollState = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setIsAtBottom(el.scrollHeight - (el.scrollTop + el.clientHeight) < 40);
  }, []);

  // ===== Close the active thread when leaving the chat page =====
  // Closing a thread triggers server-side consolidation (episode/memory
  // extraction), so we mark the session boundary on unmount — i.e. when the
  // user navigates away from chat. Previously this fired on tab-hide, which
  // closed the thread the moment you alt-tabbed away mid-conversation.
  useEffect(() => {
    if (user?.id == null) return;
    return () => {
      if (currentThreadIdRef.current) {
        api.threads.close(currentThreadIdRef.current).catch(() => {});
      }
    };
  }, [user?.id]);

  // Thread actions
  const loadThreadMessages = useCallback(
    async (threadId: number) => {
      const res = await api.threads.messages(threadId);
      setMessages(mapThreadMessages(res.messages, user?.id ?? 0));
    },
    [user?.id],
  );

  const handleSelectThread = async (threadId: number) => {
    seedActiveRef.current = false;
    pendingContextRef.current = [];
    currentThreadIdRef.current = threadId;
    setCurrentThreadId(threadId);
    setMessages([]);
    try {
      await loadThreadMessages(threadId);
    } catch {
      setCurrentThreadId(null);
      currentThreadIdRef.current = null;
      setError("Failed to load thread messages.");
    }
  };

  const handleNewThread = async () => {
    seedActiveRef.current = false;
    pendingContextRef.current = [];
    setMessages([]);
    setError("");
    try {
      // Actually start a fresh thread: the endpoint closes the active thread
      // (firing consolidation) — or reuses it if it's still empty — and returns
      // the new one. Point the conversation at it so the next reply lands there.
      const res = await api.threads.create();
      currentThreadIdRef.current = res.threadId;
      setCurrentThreadId(res.threadId);
      const list = await api.threads.list();
      setThreads(dedupeThreads(list.threads));
    } catch {
      // Local-only fallback; the next send still rotates to a fresh thread once
      // the active one is closed on leaving chat.
      currentThreadIdRef.current = null;
      setCurrentThreadId(null);
    }
  };

  const handleDeleteThread = async (threadId: number) => {
    try {
      await api.threads.delete(threadId);
      setThreads((prev) => prev.filter((t) => t.id !== threadId));
      if (currentThreadId === threadId) {
        currentThreadIdRef.current = null;
        setCurrentThreadId(null);
        setMessages([]);
      }
    } catch {
      setError("Failed to delete thread.");
    }
  };

  const handleToggleTrace = useCallback(() => {
    setShowTrace((prev) => {
      const next = !prev;
      persistShowTrace(next);
      return next;
    });
  }, []);

  // Ctrl+Shift+T — toggle trace panel
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.shiftKey && e.key === "T") {
        e.preventDefault();
        handleToggleTrace();
      }
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [handleToggleTrace]);

  // Send the last run's trace to Animus for debugging.
  // TODO: when Animus is connected, route directly to Animus session instead of chat.
  const handleDebugInAnimus = () => {
    const errorEvents = traceEvents.filter(
      (e) =>
        (e as any).type === "error" ||
        ((e as any).type === "warning" && (e as any).code === "empty_step_result") ||
        ((e as any).type === "tool_return" && (e as any).isError),
    );
    if (errorEvents.length === 0) return;

    const lines: string[] = ["Execution trace from last run:"];
    for (const e of traceEvents) {
      const ev = e as any;
      if (ev.type === "tool_call") {
        lines.push(`  → ${ev.name}(${JSON.stringify(ev.arguments ?? {})})`);
      } else if (ev.type === "tool_return") {
        lines.push(
          `  ← ${ev.name}: ${ev.isError ? "ERROR — " : ""}${String(ev.output ?? "").slice(0, 400)}`,
        );
      } else if (ev.type === "error") {
        lines.push(`  !! ${ev.error}`);
      } else if (ev.type === "warning") {
        lines.push(`  ⚠ [${ev.code}] ${ev.message}`);
      }
    }

    void sendMessage(
      `Debug the last run. Here's the trace:\n\`\`\`\n${lines.join("\n")}\n\`\`\`\nDiagnose what went wrong and fix it.`,
    );
  };

  const handleAttach = useCallback((type: string) => {
    if (type === "image") {
      fileInputRef.current?.click();
    }
  }, []);

  const handleImageSelection = (files: FileList | null) => {
    if (!files || files.length === 0) return;

    const acceptedFiles = Array.from(files).filter((file) =>
      ACCEPTED_IMAGE_TYPES.has(file.type),
    );
    if (acceptedFiles.length !== files.length) {
      setError("Unsupported image type. Use PNG, JPEG, WebP, or GIF.");
    }

    const availableSlots = MAX_SELECTED_IMAGES - selectedImages.length;
    if (availableSlots <= 0) {
      setError(`Attach at most ${MAX_SELECTED_IMAGES} images.`);
      return;
    }

    if (acceptedFiles.length > availableSlots) {
      setError(`Attach at most ${MAX_SELECTED_IMAGES} images.`);
    }

    const nextImages = acceptedFiles.slice(0, availableSlots).map((file) => {
      const previewUrl = URL.createObjectURL(file);
      objectUrlsRef.current.add(previewUrl);
      return {
        id: `local_${crypto.randomUUID()}`,
        file,
        previewUrl,
      };
    });

    setSelectedImages((prev) => [...prev, ...nextImages]);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const removeSelectedImage = useCallback((id: string) => {
    setSelectedImages((prev) => {
      const removed = prev.find((image) => image.id === id);
      if (removed) {
        revokeImagePreviews([removed]);
      }
      return prev.filter((image) => image.id !== id);
    });
  }, [revokeImagePreviews]);

  // Send message
  const sendMessage = async (
    text: string,
    contextMessages: ChatContextMessage[] = [],
    opts: { skipContextDisplay?: boolean } = {},
  ) => {
    if ((!text.trim() && selectedImages.length === 0) || user?.id == null || streaming) {
      return;
    }

    const userMsg = text.trim();
    const turnContextMessages = contextMessages.filter((message) =>
      message.content.trim(),
    );
    const activeTodayContext =
      todayContext?.date === todayIso() ? todayContext : null;
    if (todayContext && !activeTodayContext) {
      setTodayContext(null);
      saveTodayContext(null);
    }
    const suggestedTodayContext = activeTodayContext
      ? null
      : suggestTodayContextFromMessage(userMsg);
    const imagesForTurn = selectedImages;
    let requestAttachments: ChatRequestAttachment[] = [];
    try {
      requestAttachments = await Promise.all(
        imagesForTurn.map((image) => toRequestAttachment(image)),
      );
    } catch {
      setError("Failed to read image attachment.");
      return;
    }

    setInput("");
    setError("");
    setTodaySuggestion(suggestedTodayContext);

    const now = Date.now();
    // When the context is already on screen (e.g. a seeded thread's opening
    // thought), skip re-rendering it — but still send it to the server below.
    const optimisticContextMessages: ChatMessage[] = opts.skipContextDisplay
      ? []
      : turnContextMessages.map((message, index) => ({
          id: now + index,
          userId: user.id,
          role: "assistant",
          content: message.content.trim(),
          source: message.source ?? null,
          pills: message.pills ?? undefined,
        }));
    const tempUserMsg: ChatMessage = {
      id: now + optimisticContextMessages.length + 1,
      userId: user.id,
      role: "user",
      content: userMsg,
      attachments: imagesForTurn.map((image) => toPreviewAttachment(image)),
    };
    setMessages((prev) => [...prev, ...optimisticContextMessages, tempUserMsg]);
    setStreaming(true);
    setStreamBuffer("");
    setReasoningBuffer("");
    setTraceEvents([]);

    const CONTENT_RESET = "\x00CONTENT_RESET\x00";
    const REASONING_PREFIX = "\x00REASONING\x00";
    const TRACE_PREFIX = "\x00TRACE\x00";

    try {
      let fullResponse = "";
      let fullReasoning = "";
      const collectedTraces: TraceEvent[] = [];

      for await (const chunk of api.chat.stream(
        userMsg,
        user.id,
        currentThreadId ?? undefined,
        requestAttachments,
        turnContextMessages,
        activeTodayContext,
      )) {
        if (chunk.startsWith(REASONING_PREFIX)) {
          fullReasoning += chunk.slice(REASONING_PREFIX.length);
          setReasoningBuffer(fullReasoning);
          continue;
        }
        if (chunk.startsWith(TRACE_PREFIX)) {
          try {
            const evt = JSON.parse(
              chunk.slice(TRACE_PREFIX.length),
            ) as TraceEvent;
            collectedTraces.push(evt);
            setTraceEvents([...collectedTraces]);
            if (evt.type === "done" && evt.threadId != null) {
              currentThreadIdRef.current = evt.threadId;
              setCurrentThreadId(evt.threadId);
              api.threads
                .list()
                .then((res) => setThreads(dedupeThreads(res.threads)))
                .catch(() => {});
            }
          } catch {}
          continue;
        }
        if (chunk.startsWith(CONTENT_RESET)) {
          fullResponse = chunk.slice(CONTENT_RESET.length);
          setStreamBuffer(fullResponse);
          continue;
        }
        fullResponse += chunk;
        setStreamBuffer(fullResponse);
      }

      const emptyStepWarning = collectedTraces.find(
        (event) =>
          event.type === "warning" && event.code === "empty_step_result",
      );

      const assistantMsg: ChatMessage = {
        id: Date.now() + 1,
        userId: user.id,
        role: "assistant",
        content:
          fullResponse ||
          (emptyStepWarning ? "[empty model output]" : "[no response]"),
        reasoning: fullReasoning || undefined,
        traceEvents: collectedTraces.length > 0 ? collectedTraces : undefined,
      };
      setMessages((prev) => [...prev, assistantMsg]);
      const resolvedThreadId = currentThreadIdRef.current ?? currentThreadId;
      if (imagesForTurn.length > 0 && resolvedThreadId != null) {
        try {
          await loadThreadMessages(resolvedThreadId);
        } catch {
          // Keep the optimistic message if the refresh fails.
        }
      }
      setStreamBuffer("");
      setReasoningBuffer("");
      revokeImagePreviews(imagesForTurn);
      setSelectedImages([]);
    } catch (err: any) {
      setError(err.message || "Connection failed");
      setStreamBuffer((partial) => {
        if (partial) {
          const partialMsg: ChatMessage = {
            id: Date.now() + 1,
            userId: user.id,
            role: "assistant",
            content: partial + "\n\n*[connection interrupted]*",
          };
          setMessages((prev) => [...prev, partialMsg]);
        }
        return "";
      });
    } finally {
      setStreaming(false);
      setReasoningBuffer("");
    }
  };

  // Submit handler — on the first reply to a seeded thread, carry the seeded
  // thought along as context (it's already shown, so don't re-render it).
  const handleSubmit = () => {
    const seedContext = pendingContextRef.current;
    const wasSeed = seedActiveRef.current && seedContext.length > 0;
    pendingContextRef.current = [];
    seedActiveRef.current = false;
    if (wasSeed) {
      void sendMessage(input, seedContext, { skipContextDisplay: true });
    } else {
      void sendMessage(input);
    }
  };

  // Message content renderer
  const renderMessageContent = (
    content: string,
    role: string,
    message?: { attachments?: ChatAttachment[]; pills?: ChatMessage["pills"] },
  ) => {
    if (role === "user") {
      return (
        <div className="pr-6">
          <ChatImageAttachments attachments={message?.attachments} />
          {content && (
            <p className="text-sm whitespace-pre-wrap break-words leading-relaxed">
              {content}
            </p>
          )}
        </div>
      );
    }
    return (
      <div className="prose prose-invert prose-sm md:prose-base max-w-none">
        <MessagePills pills={message?.pills} />
        <ReactMarkdown
          rehypePlugins={[rehypeHighlight]}
          components={{
            pre: ({ children }) => (
              <pre className="bg-foreground/[0.06] p-3 overflow-x-auto my-2">
                {children}
              </pre>
            ),
            code: ({ children, className }) => {
              const isInline = !className;
              return isInline ? (
                <code className="bg-primary/10 text-primary px-1 py-0.5 text-[0.85em]">
                  {children}
                </code>
              ) : (
                <code className={className}>{children}</code>
              );
            },
          }}
        >
          {content}
        </ReactMarkdown>
      </div>
    );
  };

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif"
        multiple
        className="hidden"
        onChange={(event) => handleImageSelection(event.currentTarget.files)}
      />
      <ChatLayout
        input={input}
        onInputChange={setInput}
        onSubmit={handleSubmit}
        streaming={streaming}
        canSubmit={Boolean(input.trim()) || selectedImages.length > 0}
        onAttach={handleAttach}
        inputAccessory={
          <>
            <TodayContextPanel
              context={todayContext}
              greeting={todayContext ? null : todayGreeting}
              suggestion={todayContext ? null : todaySuggestion}
              onSave={handleTodayContextSave}
              onClear={handleTodayContextClear}
              onAcceptSuggestion={handleTodaySuggestionAccept}
              onDismissSuggestion={handleTodaySuggestionDismiss}
            />
            <SelectedImagePreviews
              images={selectedImages}
              onRemove={removeSelectedImage}
            />
          </>
        }
        showSidebar={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((v) => !v)}
        showTrace={showTrace}
        onToggleTrace={handleToggleTrace}
        showScrollButton={!isAtBottom}
        onScrollToBottom={() => scrollToBottom("smooth")}
        sidebar={
          sidebarOpen ? (
            <ThreadSidebar
              threads={threads}
              currentThreadId={currentThreadId}
              searchQuery={threadSearch}
              onSearchChange={setThreadSearch}
              onSelectThread={handleSelectThread}
              onNewThread={handleNewThread}
              onDeleteThread={handleDeleteThread}
              onToggleSidebar={() => setSidebarOpen(false)}
            />
          ) : undefined
        }
      >
        <div
          ref={scrollRef}
          onScroll={updateScrollState}
          className="max-w-5xl mx-auto w-full space-y-1 pb-24"
        >
          {messages.length === 0 && !streaming && <ChatEmptyState />}

          {messages.map((msg, index) => {
            const prevMsg = index > 0 ? messages[index - 1] : null;
            const isGrouped = shouldGroupMessages(msg, prevMsg);
            return USE_COMPACT_BUBBLE ? (
              <CompactChatBubble
                key={msg.id}
                message={msg}
                avatarUrl={agentAvatarUrl}
                showTrace={showTrace}
                isGrouped={isGrouped}
                onTranslate={(text) => translateText(text, translateLang)}
                renderContent={renderMessageContent}
              />
            ) : (
              <ChatBubble
                key={msg.id}
                message={msg}
                avatarUrl={agentAvatarUrl}
                showTrace={showTrace}
                isGrouped={isGrouped}
                onTranslate={(text) => translateText(text, translateLang)}
                renderContent={renderMessageContent}
              />
            );
          })}

          <StreamingView
            streaming={streaming}
            streamBuffer={streamBuffer}
            reasoningBuffer={reasoningBuffer}
            traceEvents={traceEvents}
            showTrace={showTrace}
            agentAvatarUrl={agentAvatarUrl}
          />

          {/* Debug in Animus — shows after a turn with errors in the trace */}
          {!streaming &&
            traceEvents.some(
              (e) =>
                (e as any).type === "error" ||
                ((e as any).type === "warning" &&
                  (e as any).code === "empty_step_result") ||
                ((e as any).type === "tool_return" && (e as any).isError),
            ) && (
              <div className="flex items-center gap-3 pt-1 pb-2 px-1">
                <div className="shrink-0 w-12" />
                <button
                  type="button"
                  onClick={handleDebugInAnimus}
                  className="font-mono text-[9px] tracking-[0.18em] uppercase text-yellow-400/70 hover:text-yellow-400 border border-yellow-400/30 hover:border-yellow-400/60 px-3 py-1.5 bg-yellow-400/5 hover:bg-yellow-400/10 transition-all"
                >
                  ⬡ DEBUG IN ANIMUS
                </button>
                <span className="font-mono text-[8px] text-muted-foreground/30 tracking-wider">
                  sends trace as context
                </span>
              </div>
            )}

          {error && (
            <div className="mx-10 bg-card border-l-2 border-destructive px-4 py-3 font-mono text-destructive text-[11px] tracking-wider">
              ERR: {error}
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </ChatLayout>
    </>
  );
}
