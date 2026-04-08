import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import type { ChatMessage, Thread, TraceEvent } from "@anima/api-client";
import { api } from "../../lib/api";
import { API_BASE } from "../../lib/runtime";
import { getUnlockToken } from "../../lib/api";
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

// Translate handler
async function translateText(text: string, lang: string): Promise<string> {
  return await api.translate(text, lang);
}

export default function Chat() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const pendingMsgRef = useRef<string | null>(searchParams.get("msg"));

  // Messages & input
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [translateLang] = useState(getTranslateLang());

  // Streaming state
  const [streaming, setStreaming] = useState(false);
  const [streamBuffer, setStreamBuffer] = useState("");
  const [reasoningBuffer, setReasoningBuffer] = useState("");
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [showTrace] = useState(false);

  // Thread state
  const [threads, setThreads] = useState<Thread[]>([]);
  const [currentThreadId, setCurrentThreadId] = useState<number | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [threadSearch, setThreadSearch] = useState("");
  const currentThreadIdRef = useRef<number | null>(null);

  // Avatar
  const [agentAvatarUrl, setAgentAvatarUrl] = useState<string>(personaAvatar);

  // Scroll state
  const [isAtBottom, setIsAtBottom] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const historyHydratedRef = useRef(false);

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
            pendingMsgRef.current = null;
            setSearchParams({}, { replace: true });
            setTimeout(() => sendMessage(pending), 100);
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

    return () => {
      revoked = true;
    };
  }, [user?.id]);

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
  const handleSelectThread = async (threadId: number) => {
    currentThreadIdRef.current = threadId;
    setCurrentThreadId(threadId);
    setMessages([]);
    try {
      const res = await api.threads.messages(threadId);
      const mapped: ChatMessage[] = res.messages
        .filter((m) => m.role === "user" || m.role === "assistant")
        .map((m, i) => ({
          id: i,
          userId: user?.id ?? 0,
          role: m.role as "user" | "assistant",
          content: m.content,
          createdAt: m.ts ?? undefined,
          retrieval: m.retrieval ?? undefined,
        }));
      setMessages(mapped);
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

  // Send message
  const sendMessage = async (text: string) => {
    if (!text.trim() || user?.id == null || streaming) return;

    const userMsg = text.trim();
    setInput("");
    setError("");

    const tempUserMsg: ChatMessage = {
      id: Date.now(),
      userId: user.id,
      role: "user",
      content: userMsg,
    };
    setMessages((prev) => [...prev, tempUserMsg]);
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
      setStreamBuffer("");
      setReasoningBuffer("");
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
  const renderMessageContent = (content: string, role: string) => {
    if (role === "user") {
      return (
        <p className="text-sm whitespace-pre-wrap break-words leading-relaxed pr-6">
          {content}
        </p>
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
    <ChatLayout
      input={input}
      onInputChange={setInput}
      onSubmit={() => sendMessage(input)}
      streaming={streaming}
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
  );
}
