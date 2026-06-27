import { useState, useEffect, useRef, useCallback } from "react";
import { useLocation, useSearchParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import type {
  ChatAttachment,
  ChatContextMessage,
  ChatMessage,
  ChatRequestAttachment,
  DocumentWorkflowActionResponse,
  ThreadContextStats,
  TodayContext,
  Thread,
  TraceEvent,
} from "@anima/api-client";
import { api, getRuntimeRequestHeaders } from "../../lib/api";
import { API_BASE, API_ORIGIN } from "../../lib/runtime";
import {
  loadTodayContext,
  saveTodayContext,
  todayIso,
} from "../../lib/today-context";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";

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
const MAX_SELECTED_DOCUMENTS = 4;

interface ChatLocationState {
  contextMessages?: ChatContextMessage[];
  // When true, open a fresh thread and show contextMessages as the opening
  // assistant message(s) without auto-sending a user prompt.
  seedThread?: boolean;
  // Open and display a specific existing thread by ID.
  resumeThreadId?: number;
}

interface PendingImageAttachment {
  id: string;
  file: File;
  previewUrl: string;
}

type PendingDocumentStatus = "indexing" | "indexed" | "failed";

interface PendingDocumentAttachment {
  id: string;
  file: File;
  filename: string;
  status: PendingDocumentStatus;
  workflowId?: number;
  documentId?: number;
  error?: string;
}

type ChatPill = NonNullable<ChatMessage["pills"]>[number];

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

function truncatePillLabel(label: string, limit = 64): string {
  const cleaned = label.trim().replace(/\s+/g, " ");
  if (cleaned.length <= limit) return cleaned;
  return `${cleaned.slice(0, Math.max(limit - 3, 1)).trimEnd()}...`;
}

function toDocumentAttachmentPill(
  document: Pick<PendingDocumentAttachment, "filename" | "documentId">,
): ChatPill {
  return {
    kind: "document_attachment",
    label: truncatePillLabel(document.filename),
    ref: document.documentId ?? null,
  };
}

function toDocumentSourcePill(
  document: Pick<PendingDocumentAttachment, "filename" | "documentId">,
): ChatPill {
  return {
    kind: "document_source",
    label: truncatePillLabel(document.filename),
    ref: document.documentId ?? null,
  };
}

function toImageSourcePill(image: PendingImageAttachment): ChatPill {
  return {
    kind: "image_source",
    label: truncatePillLabel(image.file.name || "Image"),
    ref: image.id,
  };
}

function buildAssistantSourcePills({
  documents,
  images,
}: {
  documents: PendingDocumentAttachment[];
  images: PendingImageAttachment[];
}): ChatPill[] | undefined {
  const pills: ChatPill[] = [];
  pills.push(...documents.map(toDocumentSourcePill));
  pills.push(...images.map(toImageSourcePill));
  return pills.length > 0 ? pills : undefined;
}

function isPdfFile(file: File): boolean {
  return file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
}

function asPdfUploadFile(file: File): File {
  if (file.type === "application/pdf") return file;
  return new File([file], file.name, { type: "application/pdf" });
}

function documentIdFromWorkflowResponse(
  response: DocumentWorkflowActionResponse,
): number | null {
  const result = response.workflow?.result;
  const resultId = readDocumentId(result);
  if (resultId !== null) return resultId;

  const checkpoints = response.workflow?.checkpoints ?? [];
  for (let index = checkpoints.length - 1; index >= 0; index -= 1) {
    const checkpoint = checkpoints[index];
    const artifactId = readDocumentId(checkpoint.artifacts);
    if (artifactId !== null) return artifactId;
    const outputId = readDocumentId(checkpoint.output);
    if (outputId !== null) return outputId;
  }
  return null;
}

function readDocumentId(value: unknown): number | null {
  if (!value || typeof value !== "object") return null;
  const payload = value as { document_id?: unknown; documentId?: unknown };
  const raw = payload.document_id ?? payload.documentId;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

function MessagePills({ pills }: { pills?: ChatMessage["pills"] }) {
  if (!pills || pills.length === 0) return null;
  return (
    <div className="mb-2 flex flex-wrap gap-1 not-prose">
      {pills.map((pill) => {
        const filePill =
          pill.kind === "document_attachment" ||
          pill.kind === "document_source" ||
          pill.kind === "image_source";
        const prefix =
          pill.kind === "document_source"
            ? "Cited PDF"
            : pill.kind === "document_attachment"
              ? "PDF"
              : "Used Image";

        return (
          <span
            key={`${pill.kind}:${String(pill.ref ?? "")}:${pill.label}`}
            className={`inline-flex max-w-full items-center gap-1 border border-border/60 px-1.5 py-0.5 ${
              filePill
                ? "bg-card/70 text-foreground/80"
                : "text-muted-foreground/55"
            }`}
          >
            {filePill ? (
              <>
                <span className="font-mono text-[8px] uppercase tracking-[0.15em] text-foreground/65">
                  {prefix}
                </span>
                <span className="max-w-[240px] truncate text-[11px] leading-none">
                  {pill.label}
                </span>
              </>
            ) : (
              <span className="font-mono text-[8px] uppercase tracking-[0.15em]">
                {pill.label}
              </span>
            )}
          </span>
        );
      })}
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
    <div className="mb-2 flex flex-wrap gap-1.5">
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
    fetch(attachmentFetchUrl(attachment.url), {
      headers: getRuntimeRequestHeaders(),
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
      className="h-20 w-auto max-w-[140px] object-cover border border-foreground/[0.08]"
    />
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
  const resumeThreadIdRef = useRef<number | null>(locationState?.resumeThreadId ?? null);

  // Messages & input
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [selectedImages, setSelectedImages] = useState<PendingImageAttachment[]>(
    [],
  );
  const [selectedDocuments, setSelectedDocuments] = useState<
    PendingDocumentAttachment[]
  >([]);
  const [todayContext, setTodayContext] = useState<TodayContext | null>(() =>
    loadTodayContext(),
  );
  const [error, setError] = useState("");
  const [translateLang] = useState(getTranslateLang());
  const fileInputRef = useRef<HTMLInputElement>(null);
  const objectUrlsRef = useRef<Set<string>>(new Set());

  // Streaming state
  const [streaming, setStreaming] = useState(false);
  const streamingRef = useRef(false);
  const [streamBuffer, setStreamBuffer] = useState("");
  const [reasoningBuffer, setReasoningBuffer] = useState("");
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [showTrace, setShowTrace] = useState(() => getShowTrace());

  // Thread state
  const [threads, setThreads] = useState<Thread[]>([]);
  const [currentThreadId, setCurrentThreadId] = useState<number | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [threadSearch, setThreadSearch] = useState("");
  const [contextStats, setContextStats] = useState<ThreadContextStats | null>(null);
  const currentThreadIdRef = useRef<number | null>(null);

  const [thinkingMonologue, setThinkingMonologue] = useState<string[]>([]);

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

    // Thinking monologue — independent of the conversation flow.
    api.consciousness
      .getAgentProfile(userId)
      .then((profile) => {
        if (!revoked) setThinkingMonologue(profile.thinkingMonologue ?? []);
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

      // 0. Opened from the dashboard "Recent Chats" node with a specific thread.
      const resumeId = resumeThreadIdRef.current;
      if (resumeId != null) {
        resumeThreadIdRef.current = null;
        const target = nextThreads.find((t) => t.id === resumeId);
        if (target) {
          currentThreadIdRef.current = resumeId;
          setCurrentThreadId(resumeId);
          setMessages([]);
          try {
            const res = await api.threads.messages(resumeId);
            if (!revoked) setMessages(mapThreadMessages(res.messages, userId));
          } catch {
            /* fall through to fresh start */
          }
          return;
        }
      }

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

      // 4. Fresh start — empty slate.
      setMessages([]);
    })();

    return () => {
      revoked = true;
    };
  }, [user?.id]);

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
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior });
  }, []);

  useEffect(() => {
    if (!historyHydratedRef.current && messages.length > 0) {
      scrollToBottom("auto");
      historyHydratedRef.current = true;
      return;
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
      // Skip close when a stream is in-flight — the server is still generating.
      // Consolidation will fire on the next natural thread close instead.
      if (currentThreadIdRef.current && !streamingRef.current) {
        api.threads.close(currentThreadIdRef.current).catch(() => {});
      }
    };
  }, [user?.id]);

  // Fetch context stats whenever the active thread changes or streaming ends
  useEffect(() => {
    if (!currentThreadId) { setContextStats(null); return; }
    if (streaming) return;
    api.threads.contextStats(currentThreadId)
      .then(setContextStats)
      .catch(() => setContextStats(null));
  }, [currentThreadId, streaming]);

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
    historyHydratedRef.current = false;
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

    const threadToClose = currentThreadIdRef.current;
    currentThreadIdRef.current = null;
    setCurrentThreadId(null);
    setMessages([]);
    setError("");

    // Close the current thread to fire consolidation, but don't eagerly
    // create a new one — it will be created on the first message send.
    try {
      if (threadToClose) await api.threads.close(threadToClose);
      const list = await api.threads.list();
      setThreads(dedupeThreads(list.threads));
    } catch {}
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

  const handleAttach = useCallback((_type: string) => {
    fileInputRef.current?.click();
  }, []);

  const uploadAndIndexDocument = useCallback(
    async (id: string, file: File) => {
      if (user?.id == null) return;
      try {
        const upload = await api.documents.uploadPdf(
          user.id,
          asPdfUploadFile(file),
          currentThreadIdRef.current ?? undefined,
        );
        const resumed = await api.documents.resumeWorkflow(upload.workflowId);
        const documentId =
          documentIdFromWorkflowResponse(resumed) ??
          documentIdFromWorkflowResponse(upload);
        if (documentId == null) {
          throw new Error("PDF indexed without returning a document id.");
        }
        setSelectedDocuments((prev) =>
          prev.map((document) =>
            document.id === id
              ? {
                  ...document,
                  status: "indexed",
                  workflowId: upload.workflowId,
                  documentId,
                  error: undefined,
                }
              : document,
          ),
        );
      } catch (err: any) {
        setSelectedDocuments((prev) =>
          prev.map((document) =>
            document.id === id
              ? {
                  ...document,
                  status: "failed",
                  error: err?.message || "PDF indexing failed.",
                }
              : document,
          ),
        );
      }
    },
    [user?.id],
  );

  const handleFileSelection = (files: FileList | null) => {
    if (!files || files.length === 0) return;

    const incomingFiles = Array.from(files);
    const acceptedImageFiles = incomingFiles.filter((file) =>
      ACCEPTED_IMAGE_TYPES.has(file.type),
    );
    const acceptedPdfFiles = incomingFiles.filter(isPdfFile);
    if (acceptedImageFiles.length + acceptedPdfFiles.length !== incomingFiles.length) {
      setError("Unsupported attachment type. Use PNG, JPEG, WebP, GIF, or PDF.");
    }

    const availableSlots = MAX_SELECTED_IMAGES - selectedImages.length;
    if (acceptedImageFiles.length > 0 && availableSlots <= 0) {
      setError(`Attach at most ${MAX_SELECTED_IMAGES} images.`);
    } else if (acceptedImageFiles.length > availableSlots) {
      setError(`Attach at most ${MAX_SELECTED_IMAGES} images.`);
    }

    const nextImages = acceptedImageFiles.slice(0, Math.max(availableSlots, 0)).map((file) => {
      const previewUrl = URL.createObjectURL(file);
      objectUrlsRef.current.add(previewUrl);
      return {
        id: `local_${crypto.randomUUID()}`,
        file,
        previewUrl,
      };
    });

    if (nextImages.length > 0) {
      setSelectedImages((prev) => [...prev, ...nextImages]);
    }

    const availableDocumentSlots = MAX_SELECTED_DOCUMENTS - selectedDocuments.length;
    if (acceptedPdfFiles.length > 0 && availableDocumentSlots <= 0) {
      setError(`Attach at most ${MAX_SELECTED_DOCUMENTS} PDFs.`);
    } else if (acceptedPdfFiles.length > availableDocumentSlots) {
      setError(`Attach at most ${MAX_SELECTED_DOCUMENTS} PDFs.`);
    }

    const nextDocuments = acceptedPdfFiles
      .slice(0, Math.max(availableDocumentSlots, 0))
      .map((file) => ({
        id: `document_${crypto.randomUUID()}`,
        file,
        filename: file.name,
        status: "indexing" as const,
      }));

    if (nextDocuments.length > 0) {
      setSelectedDocuments((prev) => [...prev, ...nextDocuments]);
      for (const document of nextDocuments) {
        void uploadAndIndexDocument(document.id, document.file);
      }
    }

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

  const removeSelectedDocument = useCallback((id: string) => {
    setSelectedDocuments((prev) =>
      prev.filter((document) => document.id !== id),
    );
  }, []);

  // Send message
  const sendMessage = async (
    text: string,
    contextMessages: ChatContextMessage[] = [],
    opts: { skipContextDisplay?: boolean } = {},
  ) => {
    const documentsForTurn = selectedDocuments.filter(
      (document) => document.status === "indexed" && document.documentId != null,
    );
    const hasIndexingDocuments = selectedDocuments.some(
      (document) => document.status === "indexing",
    );
    const hasFailedDocuments = selectedDocuments.some(
      (document) => document.status === "failed",
    );
    if (hasIndexingDocuments) {
      setError("Wait for PDF indexing to finish before sending.");
      return;
    }
    if (hasFailedDocuments) {
      setError("Remove failed PDFs before sending.");
      return;
    }
    if (
      (!text.trim() && selectedImages.length === 0 && documentsForTurn.length === 0) ||
      user?.id == null ||
      streaming
    ) {
      return;
    }

    const userMsg = text.trim() || "Summarize the selected document.";
    const turnContextMessages = contextMessages.filter((message) =>
      message.content.trim(),
    );
    const activeTodayContext =
      todayContext?.date === todayIso() ? todayContext : null;
    if (todayContext && !activeTodayContext) {
      setTodayContext(null);
      saveTodayContext(null);
    }
    const imagesForTurn = selectedImages;
    const documentIdsForTurn = documentsForTurn.map(
      (document) => document.documentId as number,
    );
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
    setSelectedImages([]);
    setSelectedDocuments([]);

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
      pills: documentsForTurn.map(toDocumentAttachmentPill),
    };
    setMessages((prev) => [...prev, ...optimisticContextMessages, tempUserMsg]);
    setStreaming(true);
    streamingRef.current = true;
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
        documentIdsForTurn,
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
        pills: buildAssistantSourcePills({
          documents: documentsForTurn,
          images: imagesForTurn,
        }),
      };
      setMessages((prev) => [...prev, assistantMsg]);
      const resolvedThreadId = currentThreadIdRef.current ?? currentThreadId;
      if (resolvedThreadId != null) {
        try {
          await loadThreadMessages(resolvedThreadId);
        } catch {
          // Keep the optimistic message if the refresh fails.
        }
      }
      setStreamBuffer("");
      setReasoningBuffer("");
      revokeImagePreviews(imagesForTurn);
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
      streamingRef.current = false;
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
          <MessagePills pills={message?.pills} />
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

  const canSubmit =
    (Boolean(input.trim()) ||
      selectedImages.length > 0 ||
      selectedDocuments.some((document) => document.status === "indexed")) &&
    !selectedDocuments.some((document) => document.status === "indexing");

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif,application/pdf,.pdf"
        multiple
        className="hidden"
        onChange={(event) => handleFileSelection(event.currentTarget.files)}
      />
      <ChatLayout
        input={input}
        onInputChange={setInput}
        onSubmit={handleSubmit}
        streaming={streaming}
        canSubmit={canSubmit}
        onAttach={handleAttach}
        attachedImages={selectedImages.map((img) => ({
          id: img.id,
          url: img.previewUrl,
          filename: img.file.name,
          onRemove: () => removeSelectedImage(img.id),
        }))}
        attachedDocuments={selectedDocuments.map((doc) => ({
          id: doc.id,
          filename: doc.filename,
          status: doc.status,
          error: doc.error,
          onRemove: () => removeSelectedDocument(doc.id),
        }))}
        showSidebar={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen((v) => !v)}
        showTrace={showTrace}
        onToggleTrace={handleToggleTrace}
        showScrollButton={!isAtBottom}
        onScrollToBottom={() => scrollToBottom("smooth")}
        scrollContainerRef={scrollRef}
        onScroll={updateScrollState}
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
              contextStats={contextStats}
            />
          ) : undefined
        }
      >
        <div className="max-w-5xl mx-auto w-full space-y-1">
          {/* empty state intentionally blank */}

          {messages.map((msg, index) => {
            const prevMsg = index > 0 ? messages[index - 1] : null;
            const isGrouped = shouldGroupMessages(msg, prevMsg);
            return USE_COMPACT_BUBBLE ? (
              <CompactChatBubble
                key={msg.id}
                message={msg}
                showTrace={showTrace}
                isGrouped={isGrouped}
                onTranslate={(text) => translateText(text, translateLang)}
                renderContent={renderMessageContent}
              />
            ) : (
              <ChatBubble
                key={msg.id}
                message={msg}
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
            thinkingMonologue={thinkingMonologue}
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

