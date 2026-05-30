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
import { getTranslateLang } from "../../lib/preferences";

// Local chat components
import {
  ThreadSidebar,
  StreamingView,
  ChatEmptyState,
  ChatLayout,
} from "../../components/chat";

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

function ProactiveNoticeCard({
  notice,
  draft,
  loading,
  onDraftChange,
  onRegenerate,
  onDismiss,
}: {
  notice: ProactiveNotice;
  draft: string;
  loading: boolean;
  onDraftChange: (value: string) => void;
  onRegenerate: () => void;
  onDismiss: () => void;
}) {
  return (
    <div className="mb-2 border border-border bg-card px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs leading-relaxed text-muted-foreground">
          {notice.message}
        </p>
        <button
          type="button"
          onClick={onDismiss}
          className="font-mono text-[9px] tracking-[0.18em] text-muted-foreground/35 hover:text-muted-foreground"
        >
          DISMISS
        </button>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <input
          value={draft}
          onChange={(event) => onDraftChange(event.currentTarget.value)}
          placeholder="customize notice..."
          className="min-w-0 flex-1 bg-background border border-border px-2 py-1.5 font-mono text-[10px] text-foreground placeholder:text-muted-foreground/30 outline-none focus:border-muted-foreground/40"
        />
        <button
          type="button"
          onClick={onRegenerate}
          disabled={loading}
          className="shrink-0 border border-border px-2.5 py-1.5 font-mono text-[9px] tracking-[0.18em] text-muted-foreground hover:text-foreground disabled:opacity-30"
        >
          {loading ? "..." : "REGEN"}
        </button>
      </div>
    </div>
  );
}

function TodayContextCard({
  context,
  greeting,
  suggestion,
  onSave,
  onClear,
  onAcceptSuggestion,
  onDismissSuggestion,
}: {
  context: TodayContext | null;
  greeting: string | null;
  suggestion: TodayContext | null;
  onSave: (draft: TodayContextDraft) => void;
  onClear: () => void;
  onAcceptSuggestion: () => void;
  onDismissSuggestion: () => void;
}) {
  const [mood, setMood] = useState(context?.mood ?? "");
  const [energy, setEnergy] = useState(context?.energy ?? "");
  const [note, setNote] = useState(context?.note ?? "");

  useEffect(() => {
    setMood(context?.mood ?? "");
    setEnergy(context?.energy ?? "");
    setNote(context?.note ?? "");
  }, [context]);

  const hasDraft = Boolean(mood.trim() || energy.trim() || note.trim());
  const hasContext = context !== null;
  const suggestionItems = suggestion
    ? [
        suggestion.mood ? `Mood: ${suggestion.mood}` : null,
        suggestion.energy ? `Energy: ${suggestion.energy}` : null,
        suggestion.note ? `Note: ${suggestion.note}` : null,
      ].filter((item): item is string => Boolean(item))
    : [];

  return (
    <div className="mb-2 border border-border bg-card px-3 py-2.5">
      <div className="mb-2 flex items-center justify-between gap-3">
        <span className="font-mono text-[9px] tracking-[0.2em] uppercase text-muted-foreground/55">
          Today
        </span>
        <div className="flex items-center gap-2">
          {hasContext && (
            <button
              type="button"
              onClick={onClear}
              className="font-mono text-[9px] tracking-[0.18em] text-muted-foreground/35 hover:text-muted-foreground"
            >
              CLEAR
            </button>
          )}
          <button
            type="button"
            onClick={() => onSave({ mood, energy, note })}
            disabled={!hasDraft}
            className="border border-border px-2.5 py-1 font-mono text-[9px] tracking-[0.18em] text-muted-foreground hover:text-foreground disabled:opacity-30"
          >
            UPDATE
          </button>
        </div>
      </div>
      {!hasContext && greeting && (
        <p className="mb-2 text-xs leading-relaxed text-muted-foreground">
          {greeting}
        </p>
      )}
      {!hasContext && suggestion && suggestionItems.length > 0 && (
        <div className="mb-2 border-t border-border/60 pt-2">
          <div className="mb-1.5 flex items-center justify-between gap-3">
            <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/45">
              Suggested
            </span>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onDismissSuggestion}
                className="font-mono text-[9px] tracking-[0.18em] text-muted-foreground/35 hover:text-muted-foreground"
              >
                DISMISS
              </button>
              <button
                type="button"
                onClick={onAcceptSuggestion}
                className="border border-border px-2 py-1 font-mono text-[9px] tracking-[0.18em] text-muted-foreground hover:text-foreground"
              >
                ACCEPT
              </button>
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {suggestionItems.map((item) => (
              <span
                key={item}
                className="max-w-full break-words border border-border/70 px-1.5 py-0.5 text-[11px] leading-snug text-muted-foreground"
              >
                {item}
              </span>
            ))}
          </div>
        </div>
      )}
      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,0.8fr)]">
        <input
          value={mood}
          onChange={(event) => setMood(event.currentTarget.value)}
          placeholder="mood"
          maxLength={80}
          className="min-w-0 bg-background border border-border px-2 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/30 outline-none focus:border-muted-foreground/40"
        />
        <input
          value={energy}
          onChange={(event) => setEnergy(event.currentTarget.value)}
          placeholder="energy"
          maxLength={40}
          className="min-w-0 bg-background border border-border px-2 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/30 outline-none focus:border-muted-foreground/40"
        />
      </div>
      <input
        value={note}
        onChange={(event) => setNote(event.currentTarget.value)}
        placeholder="note"
        maxLength={280}
        className="mt-2 w-full bg-background border border-border px-2 py-1.5 text-xs text-foreground placeholder:text-muted-foreground/30 outline-none focus:border-muted-foreground/40"
      />
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
  const [showTrace] = useState(false);
  const [proactiveNotice, setProactiveNotice] =
    useState<ProactiveNotice | null>(null);
  const [proactiveDraft, setProactiveDraft] = useState("");
  const [proactiveLoading, setProactiveLoading] = useState(false);

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

  const loadProactiveNotice = useCallback(
    async (instruction?: string) => {
      if (user?.id == null) return;
      setProactiveLoading(true);
      try {
        const result = await api.chat.proactiveNotice(user.id, instruction);
        setProactiveNotice(result.notice);
      } catch {
        setProactiveNotice(null);
      } finally {
        setProactiveLoading(false);
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

  // ===== CONSOLIDATED: Initial data loading =====
  useEffect(() => {
    if (user?.id == null) return;

    let revoked = false;

    // Load all initial data in parallel
    Promise.all([
      // Load avatar
      api.consciousness
        .getAgentProfile(user.id)
        .then(async (profile) => {
          if (!profile.avatarUrl || revoked) return;
          const token = getUnlockToken();
          const headers: Record<string, string> = token
            ? { "x-anima-unlock": token }
            : {};
          const res = await fetch(`${API_BASE}${profile.avatarUrl}`, {
            headers,
          });
          if (res.ok && !revoked) {
            setAgentAvatarUrl(URL.createObjectURL(await res.blob()));
          }
        })
        .catch(() => {}),

      // Load chat history
      api.chat
        .history(user.id)
        .then((hist) => {
          if (revoked) return;
          setMessages(hist);
          const pending = pendingMsgRef.current;
          if (pending) {
            const pendingContext = pendingContextRef.current;
            pendingMsgRef.current = null;
            pendingContextRef.current = [];
            setSearchParams({}, { replace: true });
            setTimeout(() => sendMessage(pending, pendingContext), 100);
          }
        })
        .catch(console.error),

      // Load threads
      api.threads
        .list()
        .then((res) => {
          if (revoked) return;
          const nextThreads = dedupeThreads(res.threads);
          setThreads(nextThreads);
          const active = nextThreads.find((t) => t.status === "active");
          if (active) {
            setCurrentThreadId(active.id);
            currentThreadIdRef.current = active.id;
          }
        })
        .catch(() => {}),
    ]);

    if (!pendingMsgRef.current) {
      void loadProactiveNotice();
    }

    return () => {
      revoked = true;
    };
  }, [loadProactiveNotice, user?.id]);

  // ===== CONSOLIDATED: Polling for updates =====
  useEffect(() => {
    if (user?.id == null) return;

    const interval = setInterval(async () => {
      if (streaming || currentThreadIdRef.current != null) return;
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

  // ===== CONSOLIDATED: Visibility change =====
  useEffect(() => {
    if (user?.id == null) return;

    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden" && currentThreadIdRef.current) {
        api.threads.close(currentThreadIdRef.current).catch(() => {});
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () =>
      document.removeEventListener("visibilitychange", handleVisibilityChange);
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

  const handleNewThread = () => {
    currentThreadIdRef.current = null;
    setCurrentThreadId(null);
    setMessages([]);
    setError("");
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
  ) => {
    if ((!text.trim() && selectedImages.length === 0) || user?.id == null || streaming) {
      return;
    }

    const userMsg = text.trim();
    const implicitContextMessages =
      contextMessages.length > 0
        ? contextMessages
        : proactiveNotice?.contextMessages ?? [];
    const turnContextMessages = implicitContextMessages.filter((message) =>
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
    if (turnContextMessages.length > 0) {
      setProactiveNotice(null);
    }

    const now = Date.now();
    const optimisticContextMessages: ChatMessage[] = turnContextMessages.map(
      (message, index) => ({
        id: now + index,
        userId: user.id,
        role: "assistant",
        content: message.content.trim(),
        source: message.source ?? null,
      }),
    );
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

  // Message content renderer
  const renderMessageContent = (
    content: string,
    role: string,
    message?: { attachments?: ChatAttachment[] },
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
        <ReactMarkdown
          rehypePlugins={[rehypeHighlight]}
          components={{
            pre: ({ children }) => (
              <pre className="bg-black/30 p-3 overflow-x-auto my-2">
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
        onSubmit={() => sendMessage(input)}
        streaming={streaming}
        canSubmit={Boolean(input.trim()) || selectedImages.length > 0}
        onAttach={handleAttach}
        inputAccessory={
          <>
            <TodayContextCard
              context={todayContext}
              greeting={todayContext ? null : todayGreeting}
              suggestion={todayContext ? null : todaySuggestion}
              onSave={handleTodayContextSave}
              onClear={handleTodayContextClear}
              onAcceptSuggestion={handleTodaySuggestionAccept}
              onDismissSuggestion={handleTodaySuggestionDismiss}
            />
            {proactiveNotice && (
              <ProactiveNoticeCard
                notice={proactiveNotice}
                draft={proactiveDraft}
                loading={proactiveLoading}
                onDraftChange={setProactiveDraft}
                onRegenerate={() => loadProactiveNotice(proactiveDraft)}
                onDismiss={() => setProactiveNotice(null)}
              />
            )}
            <SelectedImagePreviews
              images={selectedImages}
              onRemove={removeSelectedImage}
            />
          </>
        }
        showSidebar={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((v) => !v)}
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
