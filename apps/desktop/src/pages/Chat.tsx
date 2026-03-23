import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { api, type ChatMessage, type TraceEvent } from "../lib/api";
import { serializeTraceAsJson, serializeTraceAsText } from "./chat-trace";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";

const LANGUAGES = [
  { code: "en", label: "English" },
  { code: "es", label: "Spanish" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "pt", label: "Portuguese" },
  { code: "ja", label: "Japanese" },
  { code: "ko", label: "Korean" },
  { code: "zh", label: "Chinese" },
  { code: "ar", label: "Arabic" },
  { code: "hi", label: "Hindi" },
  { code: "tl", label: "Filipino" },
  { code: "ru", label: "Russian" },
  { code: "it", label: "Italian" },
  { code: "vi", label: "Vietnamese" },
  { code: "th", label: "Thai" },
];

const LANG_STORAGE_KEY = "anima-translate-lang";

function getDefaultLang(): string {
  return localStorage.getItem(LANG_STORAGE_KEY) || "en";
}

function setDefaultLang(code: string) {
  localStorage.setItem(LANG_STORAGE_KEY, code);
}

export default function Chat() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const pendingMsgRef = useRef<string | null>(searchParams.get("msg"));
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamBuffer, setStreamBuffer] = useState("");
  const [reasoningBuffer, setReasoningBuffer] = useState("");
  const [error, setError] = useState("");
  const [translateLang, setTranslateLang] = useState(getDefaultLang);
  const [showLangSettings, setShowLangSettings] = useState(false);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const [traceEvents, setTraceEvents] = useState<TraceEvent[]>([]);
  const [showTrace, setShowTrace] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const historyHydratedRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const langDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (user?.id == null) return;
    api.chat
      .history(user.id)
      .then((hist) => {
        setMessages(hist);
        const pending = pendingMsgRef.current;
        if (pending) {
          pendingMsgRef.current = null;
          setSearchParams({}, { replace: true });
          setTimeout(() => sendMessage(pending), 100);
        }
      })
      .catch(console.error);
  }, [user?.id]);

  useEffect(() => {
    if (user?.id == null) return;
    const interval = setInterval(async () => {
      if (streaming) return;
      try {
        const hist = await api.chat.history(user.id);
        setMessages((prev) => {
          if (hist.length > prev.length) return hist;
          return prev;
        });
      } catch {}
    }, 10_000);
    return () => clearInterval(interval);
  }, [user?.id, streaming]);

  const scrollToBottom = useCallback((behavior: ScrollBehavior = "smooth") => {
    bottomRef.current?.scrollIntoView({ behavior, block: "end" });
  }, []);

  const updateScrollState = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom =
      el.scrollHeight - (el.scrollTop + el.clientHeight);
    setIsAtBottom(distanceFromBottom < 40);
  }, []);

  useEffect(() => {
    if (!historyHydratedRef.current && messages.length > 0) {
      scrollToBottom("auto");
      historyHydratedRef.current = true;
    }
  }, [messages.length, scrollToBottom]);

  useEffect(() => {
    if (streaming || isAtBottom) {
      scrollToBottom(streaming ? "auto" : "smooth");
    }
  }, [messages, streamBuffer, streaming, isAtBottom, scrollToBottom]);

  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
      inputRef.current.style.height = `${inputRef.current.scrollHeight}px`;
    }
  }, [input]);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (
        langDropdownRef.current &&
        !langDropdownRef.current.contains(e.target as Node)
      ) {
        setShowLangSettings(false);
      }
    };
    if (showLangSettings) {
      document.addEventListener("mousedown", handleClickOutside);
      return () =>
        document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [showLangSettings]);

  const handleLangChange = useCallback((code: string) => {
    setTranslateLang(code);
    setDefaultLang(code);
    setShowLangSettings(false);
  }, []);

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
      for await (const chunk of api.chat.stream(userMsg, user.id)) {
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
      inputRef.current?.focus();
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    sendMessage(input);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const clearHistory = async () => {
    if (user?.id == null) return;
    await api.chat.clearHistory(user.id);
    setMessages([]);
    setStreamBuffer("");
    setError("");
  };

  const currentLangLabel =
    LANGUAGES.find((l) => l.code === translateLang)?.label || translateLang;

  return (
    <div className="flex flex-col h-full relative bg-bg">
      {/* Toolbar */}
      <div className="px-3 md:px-5 py-2 border-b border-border bg-bg-card/40">
        <div className="max-w-5xl mx-auto w-full flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="font-mono text-[10px] text-text-muted tracking-wider">
              CHAT
            </span>
            <div className="w-px h-3 bg-border" />
            <span className="font-mono text-[9px] text-text-muted/40 tracking-wider">
              {messages.length} MSG
            </span>
          </div>
          <div className="flex items-center gap-4">
            {/* Language selector */}
            <div className="relative" ref={langDropdownRef}>
              <button
                onClick={() => setShowLangSettings((v) => !v)}
                className="flex items-center gap-1.5 font-mono text-[9px] text-text-muted/50 hover:text-text-muted tracking-wider transition-colors"
              >
                TL:{currentLangLabel.toUpperCase()}
              </button>
              {showLangSettings && (
                <div className="absolute right-0 top-full mt-1 z-20 bg-bg-card border border-border py-1 min-w-[140px] max-h-64 overflow-y-auto">
                  <div className="px-3 py-1.5 font-mono text-[9px] text-text-muted/40 tracking-widest border-b border-border">
                    TRANSLATE TO
                  </div>
                  {LANGUAGES.map((lang) => (
                    <button
                      key={lang.code}
                      onClick={() => handleLangChange(lang.code)}
                      className={`block w-full text-left px-3 py-1.5 font-mono text-[10px] transition-colors ${
                        translateLang === lang.code
                          ? "text-primary bg-primary/[0.06]"
                          : "text-text-muted hover:text-text hover:bg-bg-input"
                      }`}
                    >
                      {lang.label}
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button
              onClick={() => setShowTrace((v) => !v)}
              className={`font-mono text-[9px] tracking-wider transition-colors ${
                showTrace
                  ? "text-primary"
                  : "text-text-muted/40 hover:text-text-muted"
              }`}
            >
              TRACE
            </button>
            <button
              onClick={clearHistory}
              className="font-mono text-[9px] text-text-muted/40 hover:text-danger tracking-wider transition-colors"
            >
              CLEAR
            </button>
          </div>
        </div>
      </div>

      {/* Messages */}
      <div
        ref={scrollRef}
        onScroll={updateScrollState}
        className="flex-1 overflow-y-auto overscroll-contain px-2.5 md:px-4 lg:px-6 py-4 md:py-6 scroll-smooth"
      >
        <div className="max-w-5xl mx-auto w-full space-y-4 md:space-y-5">
          {messages.length === 0 && !streaming && (
            <div className="flex items-center justify-center h-full min-h-[40vh]">
              <div className="text-center space-y-4">
                <div className="font-mono text-[10px] text-text-muted/20 tracking-[0.5em]">
                  //READY
                </div>
                <div className="w-8 h-px bg-border mx-auto" />
                <p className="font-mono text-text-muted/30 text-[10px] tracking-wider">
                  AWAITING INPUT
                </p>
              </div>
            </div>
          )}

          {messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              message={msg}
              translateLang={translateLang}
              showTrace={showTrace}
            />
          ))}

          {/* Live trace panel during streaming */}
          {streaming && showTrace && traceEvents.length > 0 && (
            <div className="flex gap-3 animate-in fade-in duration-200">
              <div className="font-mono text-[9px] text-yellow-500/50 pt-1.5 select-none shrink-0 w-10 text-right tracking-wider">
                TRACE
              </div>
              <div className="max-w-[86%] md:max-w-[74%] xl:max-w-[64%] w-full bg-bg-card/60 border-l-2 border-yellow-500/30 px-3 py-2 md:px-4 md:py-2.5">
                <TracePanel events={traceEvents} />
              </div>
            </div>
          )}

          {/* Reasoning indicator */}
          {streaming && reasoningBuffer && (
            <div className="flex gap-3 animate-in fade-in duration-200">
              <div className="font-mono text-[9px] text-primary/40 pt-1.5 select-none shrink-0 w-10 text-right tracking-wider">
                THINK
              </div>
              <div className="max-w-[86%] md:max-w-[74%] xl:max-w-[64%] bg-primary/[0.03] border-l-2 border-primary/20 px-3 py-2.5 md:px-4 md:py-3">
                <div className="text-[12px] text-text-muted/60 whitespace-pre-wrap break-words leading-relaxed font-mono">
                  {reasoningBuffer}
                  <span className="inline-block w-1.5 h-3 bg-primary/30 ml-0.5 animate-cursor" />
                </div>
              </div>
            </div>
          )}

          {/* Streaming content */}
          {streaming && streamBuffer && (
            <div className="flex gap-3 animate-in fade-in duration-200">
              <div className="font-mono text-[9px] text-text-muted/40 pt-1.5 select-none shrink-0 w-10 text-right tracking-wider">
                ANIMA
              </div>
              <div className="max-w-[86%] md:max-w-[74%] xl:max-w-[64%] bg-bg-card border-l-2 border-primary/30 px-3 py-2.5 md:px-4 md:py-3">
                <div className="prose prose-invert prose-sm md:prose-base max-w-none">
                  <ReactMarkdown rehypePlugins={[rehypeHighlight]}>
                    {streamBuffer}
                  </ReactMarkdown>
                  <span className="inline-block w-1.5 h-4 bg-primary/50 ml-0.5 animate-cursor" />
                </div>
              </div>
            </div>
          )}

          {/* Waiting indicator */}
          {streaming && !streamBuffer && !reasoningBuffer && (
            <div className="flex gap-3 animate-in fade-in duration-200">
              <div className="font-mono text-[9px] text-text-muted/40 pt-1.5 select-none shrink-0 w-10 text-right tracking-wider">
                ANIMA
              </div>
              <div className="max-w-[86%] md:max-w-[74%] xl:max-w-[64%] bg-bg-card border-l-2 border-border px-3 py-2.5 md:px-4 md:py-3">
                <div className="flex gap-1 items-center h-5 font-mono text-[10px] text-text-muted/40 tracking-wider">
                  <span className="animate-pulse">PROCESSING</span>
                  <span className="w-1.5 h-3 bg-text-muted/20 animate-cursor" />
                </div>
              </div>
            </div>
          )}

          {error && (
            <div className="mx-10 bg-bg-card border-l-2 border-danger px-4 py-3 font-mono text-danger text-[11px] tracking-wider">
              ERR: {error}
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {!isAtBottom && (
        <button
          onClick={() => scrollToBottom("smooth")}
          className="absolute right-3 md:right-6 bottom-20 md:bottom-24 z-20 font-mono text-[9px] px-2.5 py-1 border border-border bg-bg-card text-text-muted hover:text-text transition-colors tracking-wider"
        >
          LATEST
        </button>
      )}

      {/* Input */}
      <form
        onSubmit={handleSubmit}
        className="border-t border-border px-2.5 md:px-5 py-3 md:py-4 bg-bg-card/40"
      >
        <div className="flex gap-2.5 md:gap-3 items-end max-w-5xl mx-auto border border-border px-2.5 md:px-3 py-2 bg-bg-card">
          <div className="font-mono text-[10px] text-primary/40 pt-1.5 md:pt-2 select-none shrink-0">
            &gt;
          </div>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="..."
            disabled={streaming}
            rows={1}
            className="flex-1 bg-transparent text-[14px] md:text-sm text-text placeholder:text-text-muted/20 outline-none resize-none max-h-40 md:max-h-32 py-1 leading-relaxed"
          />
          <button
            type="submit"
            disabled={!input.trim() || streaming}
            className="font-mono text-[9px] text-text-muted/40 hover:text-primary disabled:opacity-15 tracking-wider pb-1 transition-colors"
          >
            SEND
          </button>
        </div>
      </form>
    </div>
  );
}

function MessageBubble({
  message,
  translateLang,
  showTrace,
}: {
  message: ChatMessage;
  translateLang: string;
  showTrace: boolean;
}) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const [translation, setTranslation] = useState<string | null>(null);
  const [translating, setTranslating] = useState(false);
  const [showReasoning, setShowReasoning] = useState(false);
  const [showMsgTrace, setShowMsgTrace] = useState(false);
  const hasTrace = message.traceEvents && message.traceEvents.length > 0;

  const handleTranslate = async () => {
    if (translating) return;
    if (translation) {
      setTranslation(null);
      return;
    }
    setTranslating(true);
    try {
      const result = await api.translate(message.content, translateLang);
      setTranslation(result);
    } catch {
      setTranslation("[translation failed]");
    } finally {
      setTranslating(false);
    }
  };

  const timestamp = message.createdAt
    ? (() => {
        const dt = new Date(message.createdAt);
        if (Number.isNaN(dt.getTime())) return null;
        return dt.toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
        });
      })()
    : null;

  return (
    <div className={`group flex gap-3 ${isUser ? "justify-end" : ""}`}>
      {!isUser && (
        <div className="font-mono text-[9px] text-text-muted/40 pt-1.5 select-none shrink-0 w-10 text-right tracking-wider">
          {isSystem ? "SYS" : "ANIMA"}
        </div>
      )}
      <div
        className={`flex flex-col max-w-[86%] md:max-w-[74%] xl:max-w-[64%] ${isUser ? "items-end" : ""}`}
      >
        <div
          className={`px-3 py-2.5 md:px-4 md:py-3 ${
            isUser
              ? "bg-bg-surface border border-border text-text"
              : isSystem
                ? "bg-primary/[0.04] border-l-2 border-primary/30"
                : "bg-bg-card border-l-2 border-border"
          }`}
        >
          {isUser ? (
            <p className="text-[13px] md:text-sm whitespace-pre-wrap break-words leading-relaxed">
              {message.content}
            </p>
          ) : (
            <div className="prose prose-invert prose-sm md:prose-base max-w-none">
              <ReactMarkdown rehypePlugins={[rehypeHighlight]}>
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* Translation */}
        {translating && (
          <div className="font-mono text-[10px] text-text-muted/40 mt-1.5 px-1 animate-pulse tracking-wider">
            TRANSLATING...
          </div>
        )}
        {translation && !translating && (
          <div className="mt-1.5 w-full px-3 py-2 md:px-4 md:py-2.5 bg-bg-card/60 border-l-2 border-border text-[13px] md:text-sm text-text-muted leading-relaxed">
            {translation}
          </div>
        )}

        {/* Reasoning */}
        {showReasoning && message.reasoning && (
          <div className="mt-1.5 w-full px-3 py-2 md:px-4 md:py-2.5 bg-primary/[0.03] border-l-2 border-primary/20 text-[12px] text-text-muted/60 leading-relaxed font-mono whitespace-pre-wrap break-words max-h-60 overflow-y-auto">
            {message.reasoning}
          </div>
        )}

        {/* Trace events */}
        {(showTrace || showMsgTrace) && hasTrace && (
          <div className="mt-1.5 w-full bg-bg-card/60 border-l-2 border-yellow-500/30 px-3 py-2 md:px-4 md:py-2.5 max-h-80 overflow-y-auto">
            <TracePanel events={message.traceEvents!} />
          </div>
        )}

        {/* Actions — visible on hover */}
        <div className="flex items-center gap-3 mt-1 px-1 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
          {!isUser && message.reasoning && (
            <button
              onClick={() => setShowReasoning((v) => !v)}
              className="font-mono text-[9px] text-primary/40 hover:text-primary tracking-wider transition-colors"
            >
              {showReasoning ? "HIDE" : "THINK"}
            </button>
          )}
          {!isUser && hasTrace && (
            <button
              onClick={() => setShowMsgTrace((v) => !v)}
              className="font-mono text-[9px] text-yellow-500/40 hover:text-yellow-500 tracking-wider transition-colors"
            >
              {showMsgTrace ? "HIDE" : "TRACE"}
            </button>
          )}
          <button
            onClick={handleTranslate}
            disabled={translating}
            className="font-mono text-[9px] text-text-muted/30 hover:text-text-muted tracking-wider transition-colors disabled:opacity-30"
          >
            {translation ? "HIDE" : "TL"}
          </button>
          {timestamp && (
            <span className="font-mono text-[9px] text-text-muted/20">
              {timestamp}
            </span>
          )}
          {message.source && (
            <span className="font-mono text-[7px] text-text-muted/25 ml-1">
              via {message.source}
            </span>
          )}
        </div>
      </div>
      {isUser && (
        <div className="font-mono text-[9px] text-text-muted/40 pt-1.5 select-none shrink-0 w-10 tracking-wider">
          YOU
        </div>
      )}
    </div>
  );
}

function TracePanel({ events }: { events: TraceEvent[] }) {
  const [copyState, setCopyState] = useState<"json" | "text" | null>(null);

  const handleCopy = async (mode: "json" | "text") => {
    const payload =
      mode === "json"
        ? serializeTraceAsJson(events)
        : serializeTraceAsText(events);
    await navigator.clipboard.writeText(payload);
    setCopyState(mode);
    window.setTimeout(() => setCopyState((current) => (current === mode ? null : current)), 1200);
  };

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-3 mb-1">
        <div className="font-mono text-[9px] text-yellow-500/50 tracking-widest">
          TRACE ({events.length})
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => void handleCopy("json")}
            className="font-mono text-[9px] text-yellow-500/40 hover:text-yellow-500 tracking-wider transition-colors"
          >
            {copyState === "json" ? "COPIED" : "COPY JSON"}
          </button>
          <button
            onClick={() => void handleCopy("text")}
            className="font-mono text-[9px] text-yellow-500/40 hover:text-yellow-500 tracking-wider transition-colors"
          >
            {copyState === "text" ? "COPIED" : "COPY TEXT"}
          </button>
        </div>
      </div>
      {events.map((evt, i) => (
        <TraceEntry key={i} event={evt} />
      ))}
    </div>
  );
}

function TraceEntry({ event }: { event: TraceEvent }) {
  const [expanded, setExpanded] = useState(false);

  if (event.type === "step_state") {
    const isRequest = event.phase === "request";
    const summary = isRequest
      ? `msgs:${event.messageCount ?? 0} tools:${event.allowedTools?.length ?? 0}${event.forceToolCall ? " forced" : ""}`
      : `text:${event.assistantTextChars ?? 0} tools:${event.toolCallCount ?? 0} reasoning:${event.reasoningChars ?? 0}`;
    const details = isRequest
      ? {
          allowedTools: event.allowedTools ?? [],
          forceToolCall: event.forceToolCall ?? false,
          messages: event.messages ?? [],
        }
      : {
          assistantTextChars: event.assistantTextChars ?? 0,
          assistantTextPreview: event.assistantTextPreview ?? "",
          toolCallCount: event.toolCallCount ?? 0,
          reasoningChars: event.reasoningChars ?? 0,
          reasoningCaptured: event.reasoningCaptured ?? false,
        };

    return (
      <div className="font-mono text-[11px]">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 text-left w-full hover:bg-bg-input/30 px-1 py-0.5 -mx-1 transition-colors"
        >
          <span className="text-cyan-400/70 text-[9px]">STEP</span>
          <span className="text-text-muted/70">#{event.stepIndex ?? 0}</span>
          <span className="text-text-muted">
            {isRequest ? "request" : "result"}
          </span>
          <span className="text-text-muted/45">{summary}</span>
          <span className="text-text-muted/30 text-[9px] ml-auto">
            {expanded ? "â–¼" : "â–¶"}
          </span>
        </button>
        {expanded && (
          <pre className="text-[10px] text-text-muted/50 bg-bg-input/20 px-2 py-1.5 mt-0.5 overflow-x-auto max-h-48 whitespace-pre-wrap break-words">
            {formatJson(details)}
          </pre>
        )}
      </div>
    );
  }

  if (event.type === "warning") {
    return (
      <div className="font-mono text-[10px] text-amber-400/70 flex items-start gap-2 px-1 py-0.5">
        <span className="text-[9px]">WARN</span>
        <span className="text-amber-300/80">
          #{event.stepIndex ?? 0} {event.code}
        </span>
        {event.message && (
          <span className="text-text-muted/55">{event.message}</span>
        )}
      </div>
    );
  }

  if (event.type === "tool_call") {
    return (
      <div className="font-mono text-[11px]">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 text-left w-full hover:bg-bg-input/30 px-1 py-0.5 -mx-1 transition-colors"
        >
          <span className="text-yellow-500/70 text-[9px]">CALL</span>
          <span className="text-text-muted">{event.name}</span>
          <span className="text-text-muted/30 text-[9px] ml-auto">
            {expanded ? "▼" : "▶"}
          </span>
        </button>
        {expanded && event.arguments != null && (
          <pre className="text-[10px] text-text-muted/50 bg-bg-input/20 px-2 py-1.5 mt-0.5 overflow-x-auto max-h-40 whitespace-pre-wrap break-words">
            {formatJson(event.arguments)}
          </pre>
        )}
      </div>
    );
  }

  if (event.type === "tool_return") {
    return (
      <div className="font-mono text-[11px]">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 text-left w-full hover:bg-bg-input/30 px-1 py-0.5 -mx-1 transition-colors"
        >
          <span
            className={`text-[9px] ${event.isError ? "text-danger" : "text-emerald-500/70"}`}
          >
            {event.isError ? "ERR" : "RET"}
          </span>
          <span className="text-text-muted">{event.name}</span>
          <span className="text-text-muted/30 text-[9px] ml-auto">
            {expanded ? "▼" : "▶"}
          </span>
        </button>
        {expanded && event.output && (
          <pre className="text-[10px] text-text-muted/50 bg-bg-input/20 px-2 py-1.5 mt-0.5 overflow-x-auto max-h-40 whitespace-pre-wrap break-words">
            {event.output}
          </pre>
        )}
      </div>
    );
  }

  if (event.type === "usage") {
    return (
      <div className="font-mono text-[10px] text-text-muted/40 flex items-center gap-2 px-1 py-0.5">
        <span className="text-blue-400/60 text-[9px]">TOKENS</span>
        <span>{event.promptTokens ?? 0}in</span>
        <span>{event.completionTokens ?? 0}out</span>
        {event.reasoningTokens ? (
          <span>{event.reasoningTokens}reason</span>
        ) : null}
        {event.cachedInputTokens ? (
          <span>{event.cachedInputTokens}cached</span>
        ) : null}
        <span className="text-text-muted/25">= {event.totalTokens ?? 0}</span>
      </div>
    );
  }

  if (event.type === "timing") {
    return (
      <div className="font-mono text-[10px] text-text-muted/40 flex items-center gap-2 px-1 py-0.5">
        <span className="text-blue-400/60 text-[9px]">TIME</span>
        {event.stepIndex != null && <span>#{event.stepIndex}</span>}
        {event.ttftMs != null && <span>ttft:{event.ttftMs}ms</span>}
        {event.llmDurationMs != null && (
          <span>llm:{event.llmDurationMs}ms</span>
        )}
        {event.stepDurationMs != null && (
          <span>step:{event.stepDurationMs}ms</span>
        )}
      </div>
    );
  }

  if (event.type === "done") {
    return (
      <div className="font-mono text-[10px] text-text-muted/40 flex items-center gap-2 px-1 py-0.5">
        <span className="text-emerald-500/60 text-[9px]">DONE</span>
        {event.provider && <span>{event.provider}</span>}
        {event.model && (
          <span className="text-text-muted/25">{event.model}</span>
        )}
        {event.toolsUsed && event.toolsUsed.length > 0 && (
          <span className="text-yellow-500/40">
            tools:[{event.toolsUsed.join(",")}]
          </span>
        )}
        {event.stopReason && (
          <span className="text-text-muted/25">stop:{event.stopReason}</span>
        )}
      </div>
    );
  }

  if (event.type === "approval_pending") {
    return (
      <div className="font-mono text-[11px]">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 text-left w-full hover:bg-bg-input/30 px-1 py-0.5 -mx-1 transition-colors"
        >
          <span className="text-orange-400/70 text-[9px]">WAIT</span>
          <span className="text-text-muted">{event.name}</span>
          {event.runId != null && (
            <span className="text-text-muted/30">run:{event.runId}</span>
          )}
          <span className="text-text-muted/30 text-[9px] ml-auto">
            {expanded ? "â–¼" : "â–¶"}
          </span>
        </button>
        {expanded && (
          <pre className="text-[10px] text-text-muted/50 bg-bg-input/20 px-2 py-1.5 mt-0.5 overflow-x-auto max-h-40 whitespace-pre-wrap break-words">
            {formatJson({
              runId: event.runId,
              name: event.name,
              callId: event.callId,
              arguments: event.arguments,
            })}
          </pre>
        )}
      </div>
    );
  }

  if (event.type === "cancelled") {
    return (
      <div className="font-mono text-[10px] text-text-muted/40 flex items-center gap-2 px-1 py-0.5">
        <span className="text-rose-400/70 text-[9px]">CANCEL</span>
        {event.runId != null && <span>run:{event.runId}</span>}
      </div>
    );
  }

  return null;
}

function formatJson(value: unknown): string {
  try {
    if (typeof value === "string") {
      return JSON.stringify(JSON.parse(value), null, 2);
    }
    return JSON.stringify(value, null, 2);
  } catch {
    return typeof value === "string" ? value : String(value);
  }
}
