import { useState, useEffect, useRef, useCallback } from "react";
import { useAuth } from "../../context/AuthContext";
import type { ChatAttachment, ChatMessage, Thread, TraceEvent } from "@anima/api-client";
import { api } from "../../lib/api";
import {
  CompactChatBubble,
  shouldGroupMessages,
  ArrowRightIcon,
  ChatIcon,
  PromptInput,
  XIcon,
} from "@anima/standard-templates";
import { StreamingView } from "../../components/chat";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";

function mapMessages(
  messages: Array<{
    id?: number | null;
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
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m, i) => ({
      id: m.id ?? i,
      userId,
      role: m.role as "user" | "assistant",
      content: m.content,
      createdAt: m.ts ?? undefined,
      retrieval: m.retrieval ?? undefined,
      attachments: m.attachments ?? [],
      pills: m.pills ?? undefined,
    }));
}

interface Props {
  thread: Thread;
  onClose: () => void;
  onOpenFull: () => void;
}

export function ThreadPreviewModal({ thread, onClose, onOpenFull }: Props) {
  const { user } = useAuth();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const streamingRef = useRef(false);
  const [streamBuffer, setStreamBuffer] = useState("");
  const [reasoningBuffer, setReasoningBuffer] = useState("");
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [error, setError] = useState("");
  const currentThreadIdRef = useRef<number>(thread.id);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const loadMessages = useCallback(
    async (threadId: number) => {
      const res = await api.threads.messages(threadId);
      setMessages(mapMessages(res.messages, user?.id ?? 0));
    },
    [user?.id],
  );

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.threads
      .messages(thread.id)
      .then((r) => {
        if (!active) return;
        setMessages(mapMessages(r.messages, user?.id ?? 0));
      })
      .catch(() => {})
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [thread.id, user?.id]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamBuffer]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const sendMessage = useCallback(
    async (text: string) => {
      if (!text.trim() || !user?.id || streamingRef.current) return;

      setInput("");
      setError("");

      const tempMsg: ChatMessage = {
        id: Date.now(),
        userId: user.id,
        role: "user",
        content: text.trim(),
      };
      setMessages((prev) => [...prev, tempMsg]);
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
          text.trim(),
          user.id,
          currentThreadIdRef.current,
          [],
          [],
          null,
          [],
        )) {
          if (chunk.startsWith(REASONING_PREFIX)) {
            fullReasoning += chunk.slice(REASONING_PREFIX.length);
            setReasoningBuffer(fullReasoning);
            continue;
          }
          if (chunk.startsWith(TRACE_PREFIX)) {
            try {
              const evt = JSON.parse(chunk.slice(TRACE_PREFIX.length)) as TraceEvent;
              collectedTraces.push(evt);
              setTraceEvents([...collectedTraces]);
              if (evt.type === "done" && evt.threadId != null) {
                currentThreadIdRef.current = evt.threadId;
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

        const assistantMsg: ChatMessage = {
          id: Date.now() + 1,
          userId: user.id,
          role: "assistant",
          content: fullResponse || "[no response]",
          reasoning: fullReasoning || undefined,
          traceEvents: collectedTraces.length > 0 ? collectedTraces : undefined,
        };
        setMessages((prev) => [...prev, assistantMsg]);
        try {
          await loadMessages(currentThreadIdRef.current);
        } catch {}
        setStreamBuffer("");
        setReasoningBuffer("");
      } catch (err: any) {
        setError(err.message || "Connection failed");
        setStreamBuffer("");
      } finally {
        setStreaming(false);
        streamingRef.current = false;
      }
    },
    [user?.id, loadMessages],
  );

  const renderMessageContent = (content: string, role: string) => {
    if (role === "user") {
      return (
        <p className="text-sm whitespace-pre-wrap break-words leading-relaxed">
          {content}
        </p>
      );
    }
    return (
      <div className="prose prose-invert prose-sm max-w-none">
        <ReactMarkdown rehypePlugins={[rehypeHighlight]}>{content}</ReactMarkdown>
      </div>
    );
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/60 backdrop-blur-sm"
      onClick={onClose}
    >
      {/* NodeShell-style container */}
      <div
        className="group relative bg-background/20 backdrop-blur-[36px] border border-accent/20 shadow-[0_6px_32px_rgba(0,0,0,0.22)] flex flex-col w-full max-w-4xl mx-6 overflow-visible"
        style={{ height: "65vh" }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Floating action bar — exact NodeShell pattern, appears above on hover */}
        <div
          className="absolute -top-8 right-0 z-20 flex items-stretch h-6 opacity-0 group-hover:opacity-100 transition-all duration-200"
          style={{
            background: "color-mix(in oklch, var(--color-background) 90%, var(--color-accent) 10%)",
            clipPath: "polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)",
            filter: "drop-shadow(0 0 6px color-mix(in oklch, var(--color-accent) 60%, transparent))",
          }}
        >
          <button
            onClick={onOpenFull}
            className="px-2 flex items-center text-muted-foreground/50 hover:text-foreground hover:bg-accent transition-colors"
          >
            <ArrowRightIcon size="sm" className="-rotate-45" />
          </button>
          <button
            onClick={onClose}
            className="px-2 flex items-center text-muted-foreground/50 hover:text-foreground hover:bg-accent transition-colors"
            aria-label="Close"
          >
            <XIcon size="sm" strokeWidth={2} />
          </button>
        </div>

        {/* NodeShell header */}
        <div className="px-3.5 h-9 flex items-center bg-accent/10 border-b border-hairline-faint shrink-0 overflow-hidden">
          <div className="min-w-0 flex items-center gap-1.5">
            <ChatIcon size="sm" className="text-muted-foreground shrink-0" />
            <span className="font-mono font-semibold text-label uppercase text-foreground truncate">
              {thread.title ?? "Chat"}
            </span>
          </div>
        </div>

        {/* Messages area — flex-1, relative so the floating input can anchor to it */}
        <div className="flex-1 relative overflow-hidden">
          <div
            ref={scrollRef}
            className="h-full overflow-y-auto overscroll-contain px-3 pt-4 scroll-smooth nowheel"
          >
            <div className="max-w-3xl mx-auto w-full space-y-1 pb-[190px]">
              {loading && (
                <div className="flex items-center justify-center pt-16">
                  <span className="font-mono text-micro tracking-widest text-muted-foreground/40 uppercase animate-pulse">
                    loading...
                  </span>
                </div>
              )}

              {!loading &&
                messages.map((msg, i) => {
                  const prev = i > 0 ? messages[i - 1] : null;
                  return (
                    <CompactChatBubble
                      key={msg.id}
                      message={msg}
                      showTrace={false}
                      isGrouped={shouldGroupMessages(msg, prev)}
                      renderContent={renderMessageContent}
                    />
                  );
                })}

              <StreamingView
                streaming={streaming}
                streamBuffer={streamBuffer}
                reasoningBuffer={reasoningBuffer}
                traceEvents={traceEvents}
                showTrace={false}
                thinkingMonologue={[]}
              />

              {error && (
                <div className="mx-10 bg-card border-l-2 border-destructive px-4 py-3 font-mono text-destructive text-detail tracking-wider">
                  ERR: {error}
                </div>
              )}

              <div ref={bottomRef} />
            </div>
          </div>

          {/* Floating PromptInput — exact same pattern as ChatLayout */}
          <div className="absolute bottom-0 left-0 right-0 z-10 px-4 pt-8 pb-5 pointer-events-none">
            <div className="max-w-3xl mx-auto w-full pointer-events-auto">
              <PromptInput
                value={input}
                onChange={setInput}
                onSubmit={sendMessage}
                disabled={streaming}
                canSubmit={!!input.trim() && !streaming}
                showMic={true}
                placeholder="type something..."
              />
              <div className="mt-2 h-4 flex items-center justify-center">
                {streaming && (
                  <span className="font-mono text-micro text-accent/50 tracking-caps-4 uppercase animate-pulse">
                    processing...
                  </span>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
