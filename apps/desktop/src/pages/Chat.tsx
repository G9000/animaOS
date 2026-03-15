import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { api, type ChatMessage } from "../lib/api";
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
const AI_LABEL = "ANIMA";
const USER_LABEL = "YOU";

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
  const [error, setError] = useState("");
  const [translateLang, setTranslateLang] = useState(getDefaultLang);
  const [showLangSettings, setShowLangSettings] = useState(false);
  const [isAtBottom, setIsAtBottom] = useState(true);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const historyHydratedRef = useRef(false);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const langDropdownRef = useRef<HTMLDivElement>(null);

  // Load history on mount, then auto-send pending message from dashboard
  useEffect(() => {
    if (!user?.id) return;
    api.chat
      .history(user.id)
      .then((hist) => {
        setMessages(hist);
        // Auto-send message passed from dashboard
        const pending = pendingMsgRef.current;
        if (pending) {
          pendingMsgRef.current = null;
          setSearchParams({}, { replace: true });
          setTimeout(() => sendMessage(pending), 100);
        }
      })
      .catch(console.error);
  }, [user?.id]);

  // Poll for new messages (e.g. from task reminders) every 30s
  useEffect(() => {
    if (!user?.id) return;
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

  // Initial snap after first history load only
  useEffect(() => {
    if (!historyHydratedRef.current && messages.length > 0) {
      scrollToBottom("auto");
      historyHydratedRef.current = true;
    }
  }, [messages.length, scrollToBottom]);

  // Auto-scroll only when user is near bottom or while streaming
  useEffect(() => {
    if (streaming || isAtBottom) {
      scrollToBottom(streaming ? "auto" : "smooth");
    }
  }, [messages, streamBuffer, streaming, isAtBottom, scrollToBottom]);

  // Auto-resize textarea
  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
      inputRef.current.style.height = `${inputRef.current.scrollHeight}px`;
    }
  }, [input]);

  // Close language dropdown on outside click
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
    if (!text.trim() || !user?.id || streaming) return;

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

    try {
      let fullResponse = "";
      for await (const chunk of api.chat.stream(userMsg, user.id)) {
        fullResponse += chunk;
        setStreamBuffer(fullResponse);
      }

      const assistantMsg: ChatMessage = {
        id: Date.now() + 1,
        userId: user.id,
        role: "assistant",
        content: fullResponse || "[no response]",
      };
      setMessages((prev) => [...prev, assistantMsg]);
      setStreamBuffer("");
    } catch (err: any) {
      setError(err.message || "Connection failed");
      // Preserve any partial streamed content as a visible message
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
    if (!user?.id) return;
    await api.chat.clearHistory(user.id);
    setMessages([]);
    setStreamBuffer("");
    setError("");
  };

  const currentLangLabel =
    LANGUAGES.find((l) => l.code === translateLang)?.label || translateLang;

  return (
    <div className="flex flex-col h-full relative bg-[radial-gradient(circle_at_15%_20%,rgba(168,168,192,0.09),transparent_28%),radial-gradient(circle_at_85%_0%,rgba(168,168,192,0.06),transparent_30%)]">
      {/* Toolbar */}
      <div className="px-3 md:px-5 py-2.5 border-b border-border bg-bg/85 backdrop-blur-sm">
        <div className="max-w-5xl mx-auto w-full flex items-center justify-between">
          <span className="text-[11px] text-text-muted uppercase tracking-wider">
            Chat
          </span>
          <div className="flex items-center gap-3">
            {/* Language selector */}
            <div className="relative" ref={langDropdownRef}>
              <button
                onClick={() => setShowLangSettings((v) => !v)}
                className="flex items-center gap-1.5 text-[10px] text-text-muted hover:text-text uppercase tracking-wider transition-colors"
              >
                TL {currentLangLabel}
              </button>
              {showLangSettings && (
                <div className="absolute right-0 top-full mt-2 z-20 bg-bg-card border border-border rounded-sm py-1 shadow-xl min-w-[140px] max-h-64 overflow-y-auto">
                  <div className="px-3 py-1.5 text-[10px] text-text-muted uppercase tracking-widest border-b border-border">
                    Translate to
                  </div>
                  {LANGUAGES.map((lang) => (
                    <button
                      key={lang.code}
                      onClick={() => handleLangChange(lang.code)}
                      className={`block w-full text-left px-3 py-1.5 text-xs transition-colors ${
                        translateLang === lang.code
                          ? "text-text bg-bg-input"
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
              onClick={clearHistory}
              className="text-[10px] text-text-muted hover:text-danger uppercase tracking-wider transition-colors"
            >
              Clear
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
            <div className="flex items-center justify-center h-full min-h-[35vh]">
              <div className="text-center space-y-3">
                <div className="text-2xl text-text-muted/20 tracking-widest">
                  ◈
                </div>
                <p className="text-text-muted text-xs tracking-wider uppercase">
                  Ready
                </p>
              </div>
            </div>
          )}

          {messages.map((msg) => (
            <MessageBubble
              key={msg.id}
              message={msg}
              translateLang={translateLang}
            />
          ))}

          {/* Streaming indicator */}
          {streaming && streamBuffer && (
            <div className="flex gap-3 animate-in fade-in duration-200">
              <div className="text-[10px] text-text-muted/70 pt-1.5 select-none shrink-0 w-8 text-right uppercase">
                {AI_LABEL}
              </div>
              <div className="max-w-[86%] md:max-w-[74%] xl:max-w-[64%] bg-bg-card border border-border rounded-md px-3 py-2.5 md:px-4 md:py-3">
                <div className="prose prose-invert prose-sm md:prose-base max-w-none">
                  <ReactMarkdown rehypePlugins={[rehypeHighlight]}>
                    {streamBuffer}
                  </ReactMarkdown>
                  <span className="inline-block w-1.5 h-4 bg-primary ml-0.5 animate-pulse" />
                </div>
              </div>
            </div>
          )}

          {streaming && !streamBuffer && (
            <div className="flex gap-3 animate-in fade-in duration-200">
              <div className="text-[10px] text-text-muted/70 pt-1.5 select-none shrink-0 w-8 text-right uppercase">
                {AI_LABEL}
              </div>
              <div className="max-w-[86%] md:max-w-[74%] xl:max-w-[64%] bg-bg-card border border-border rounded-md px-3 py-2.5 md:px-4 md:py-3">
                <div className="flex gap-1.5 items-center h-5">
                  <span className="w-1.5 h-1.5 bg-text-muted rounded-full animate-bounce [animation-delay:0ms]" />
                  <span className="w-1.5 h-1.5 bg-text-muted rounded-full animate-bounce [animation-delay:150ms]" />
                  <span className="w-1.5 h-1.5 bg-text-muted rounded-full animate-bounce [animation-delay:300ms]" />
                </div>
              </div>
            </div>
          )}

          {error && (
            <div className="mx-8 bg-bg-card border border-danger/30 rounded px-4 py-3 text-danger text-sm">
              {error}
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>

      {!isAtBottom && (
        <button
          onClick={() => scrollToBottom("smooth")}
          className="absolute right-3 md:right-6 bottom-20 md:bottom-24 z-20 text-[10px] px-2.5 py-1 rounded-full border border-border bg-bg-card/90 backdrop-blur text-text-muted hover:text-text transition-colors uppercase tracking-wider"
        >
          Latest
        </button>
      )}

      {/* Input */}
      <form
        onSubmit={handleSubmit}
        className="border-t border-border px-2.5 md:px-5 py-3 md:py-4 bg-bg-card/60 backdrop-blur-sm"
      >
        <div className="flex gap-2.5 md:gap-3 items-end max-w-5xl mx-auto border border-border rounded-md px-2.5 md:px-3 py-2 bg-bg-card">
          <div className="text-[11px] md:text-xs text-primary/60 pt-1.5 md:pt-2 select-none shrink-0">
            ▸
          </div>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Say something..."
            disabled={streaming}
            rows={1}
            className="flex-1 bg-transparent text-[14px] md:text-sm text-text placeholder:text-text-muted/40 outline-none resize-none max-h-40 md:max-h-32 py-1 leading-relaxed"
          />
          <button
            type="submit"
            disabled={!input.trim() || streaming}
            className="text-[10px] text-text-muted hover:text-text disabled:opacity-20 uppercase tracking-wider pb-1 transition-colors"
          >
            Send
          </button>
        </div>
      </form>
    </div>
  );
}

function MessageBubble({
  message,
  translateLang,
}: {
  message: ChatMessage;
  translateLang: string;
}) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const [translation, setTranslation] = useState<string | null>(null);
  const [translating, setTranslating] = useState(false);

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
        <div className="text-[10px] text-text-muted/70 pt-1.5 select-none shrink-0 w-8 text-right uppercase">
          {isSystem ? "SYS" : AI_LABEL}
        </div>
      )}
      <div
        className={`flex flex-col max-w-[86%] md:max-w-[74%] xl:max-w-[64%] ${isUser ? "items-end" : ""}`}
      >
        <div
          className={`rounded-md px-3 py-2.5 md:px-4 md:py-3 shadow-[0_0_0_1px_rgba(255,255,255,0.02)] ${
            isUser
              ? "bg-linear-to-b from-bg-input to-bg-card border border-border text-text"
              : isSystem
                ? "bg-primary/[0.06] border border-primary/20"
                : "bg-bg-card/85 border border-border"
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

        {/* Translation result */}
        {translating && (
          <div className="text-[11px] text-text-muted/60 mt-1.5 px-1 animate-pulse">
            Translating...
          </div>
        )}
        {translation && !translating && (
          <div className="mt-1.5 w-full px-3 py-2 md:px-4 md:py-2.5 rounded bg-bg-card/60 border border-border/40 text-[13px] md:text-sm text-text-muted leading-relaxed">
            {translation}
          </div>
        )}

        {/* Actions row — visible on hover */}
        <div className="flex items-center gap-3 mt-1 px-1 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
          <button
            onClick={handleTranslate}
            disabled={translating}
            className="text-[10px] text-text-muted/50 hover:text-text-muted uppercase tracking-wider transition-colors disabled:opacity-30"
          >
            {translation ? "HIDE" : "TL"}
          </button>
          {timestamp && (
            <span className="text-[10px] text-text-muted/30">{timestamp}</span>
          )}
        </div>
      </div>
      {isUser && (
        <div className="text-[10px] text-text-muted/70 pt-1.5 select-none shrink-0 w-8 uppercase">
          {USER_LABEL}
        </div>
      )}
    </div>
  );
}
