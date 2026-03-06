import { useState, useEffect, useRef, useCallback } from "react";
import { useAuth } from "../context/AuthContext";
import { api, type ChatMessage } from "../lib/api";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeRaw from "rehype-raw";
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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamBuffer, setStreamBuffer] = useState("");
  const [error, setError] = useState("");
  const [translateLang, setTranslateLang] = useState(getDefaultLang);
  const [showLangSettings, setShowLangSettings] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const langDropdownRef = useRef<HTMLDivElement>(null);

  // Load history on mount
  useEffect(() => {
    if (user?.id) {
      api.chat.history(user.id).then(setMessages).catch(console.error);
    }
  }, [user?.id]);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamBuffer]);

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

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !user?.id || streaming) return;

    const userMsg = input.trim();
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
    } finally {
      setStreaming(false);
      inputRef.current?.focus();
    }
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
  };

  const currentLangLabel =
    LANGUAGES.find((l) => l.code === translateLang)?.label || translateLang;

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-5 py-2.5 border-b border-(--color-border)">
        <span className="text-[11px] text-(--color-text-muted) uppercase tracking-wider">
          Chat
        </span>
        <div className="flex items-center gap-3">
          {/* Language selector */}
          <div className="relative" ref={langDropdownRef}>
            <button
              onClick={() => setShowLangSettings((v) => !v)}
              className="flex items-center gap-1.5 text-[10px] text-(--color-text-muted) hover:text-(--color-text) uppercase tracking-wider transition-colors"
            >
              TL {currentLangLabel}
            </button>
            {showLangSettings && (
              <div className="absolute right-0 top-full mt-2 z-20 bg-(--color-bg-card) border border-(--color-border) rounded-sm py-1 shadow-xl min-w-[140px] max-h-64 overflow-y-auto">
                <div className="px-3 py-1.5 text-[10px] text-(--color-text-muted) uppercase tracking-widest border-b border-(--color-border)">
                  Translate to
                </div>
                {LANGUAGES.map((lang) => (
                  <button
                    key={lang.code}
                    onClick={() => handleLangChange(lang.code)}
                    className={`block w-full text-left px-3 py-1.5 text-xs transition-colors ${
                      translateLang === lang.code
                        ? "text-(--color-text) bg-(--color-bg-input)"
                        : "text-(--color-text-muted) hover:text-(--color-text) hover:bg-(--color-bg-input)"
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
            className="text-[10px] text-(--color-text-muted) hover:text-(--color-danger) uppercase tracking-wider transition-colors"
          >
            Clear
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-6 space-y-5">
        {messages.length === 0 && !streaming && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center space-y-3">
              <div className="text-2xl text-(--color-text-muted)/20 tracking-widest">
                ◈
              </div>
              <p className="text-(--color-text-muted) text-xs tracking-wider uppercase">
                Ready
              </p>
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} translateLang={translateLang} />
        ))}

        {/* Streaming indicator */}
        {streaming && streamBuffer && (
          <div className="flex gap-3 animate-in fade-in duration-200">
            <div className="text-[10px] text-(--color-text-muted) pt-1.5 select-none shrink-0 w-7 text-right uppercase">
              sys
            </div>
            <div className="bg-(--color-bg-card) border border-(--color-border) rounded px-4 py-3 max-w-[80%]">
              <div className="prose prose-invert prose-sm max-w-none">
                <ReactMarkdown rehypePlugins={[rehypeHighlight, rehypeRaw]}>
                  {streamBuffer}
                </ReactMarkdown>
                <span className="inline-block w-1.5 h-4 bg-(--color-primary) ml-0.5 animate-pulse" />
              </div>
            </div>
          </div>
        )}

        {streaming && !streamBuffer && (
          <div className="flex gap-3 animate-in fade-in duration-200">
            <div className="text-[10px] text-(--color-text-muted) pt-1.5 select-none shrink-0 w-7 text-right uppercase">
              sys
            </div>
            <div className="bg-(--color-bg-card) border border-(--color-border) rounded px-4 py-3">
              <div className="flex gap-1.5 items-center h-5">
                <span className="w-1.5 h-1.5 bg-(--color-text-muted) rounded-full animate-bounce [animation-delay:0ms]" />
                <span className="w-1.5 h-1.5 bg-(--color-text-muted) rounded-full animate-bounce [animation-delay:150ms]" />
                <span className="w-1.5 h-1.5 bg-(--color-text-muted) rounded-full animate-bounce [animation-delay:300ms]" />
              </div>
            </div>
          </div>
        )}

        {error && (
          <div className="mx-8 bg-(--color-bg-card) border border-(--color-danger)/30 rounded px-4 py-3 text-(--color-danger) text-sm">
            {error}
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <form
        onSubmit={handleSubmit}
        className="border-t border-(--color-border) px-5 py-4 bg-(--color-bg-card)/30"
      >
        <div className="flex gap-3 items-end max-w-4xl mx-auto">
          <div className="text-xs text-(--color-primary)/60 pt-2 select-none shrink-0">
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
            className="flex-1 bg-transparent text-sm text-(--color-text) placeholder:text-(--color-text-muted)/40 outline-none resize-none max-h-32 py-1 leading-relaxed"
          />
          <button
            type="submit"
            disabled={!input.trim() || streaming}
            className="text-xs text-(--color-text-muted) hover:text-(--color-text) disabled:opacity-20 uppercase tracking-wider pb-1 transition-colors"
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
    ? new Date(message.createdAt).toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
      })
    : null;

  return (
    <div className={`group flex gap-3 ${isUser ? "justify-end" : ""}`}>
      {!isUser && (
        <div className="text-[10px] text-(--color-text-muted) pt-1.5 select-none shrink-0 w-7 text-right uppercase">
          sys
        </div>
      )}
      <div className={`flex flex-col max-w-[80%] ${isUser ? "items-end" : ""}`}>
        <div
          className={`rounded px-4 py-3 ${
            isUser
              ? "bg-(--color-bg-input) border border-(--color-border)"
              : "bg-(--color-bg-card) border border-(--color-border)"
          }`}
        >
          {isUser ? (
            <p className="text-sm whitespace-pre-wrap break-words leading-relaxed">
              {message.content}
            </p>
          ) : (
            <div className="prose prose-invert prose-sm max-w-none">
              <ReactMarkdown rehypePlugins={[rehypeHighlight, rehypeRaw]}>
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* Translation result */}
        {translating && (
          <div className="text-[11px] text-(--color-text-muted)/60 mt-1.5 px-1 animate-pulse">
            Translating...
          </div>
        )}
        {translation && !translating && (
          <div className="mt-1.5 px-4 py-2.5 rounded bg-(--color-bg-card)/60 border border-(--color-border)/40 text-sm text-(--color-text-muted) leading-relaxed">
            {translation}
          </div>
        )}

        {/* Actions row — visible on hover */}
        <div className="flex items-center gap-3 mt-1 px-1 opacity-0 group-hover:opacity-100 transition-opacity duration-150">
          <button
            onClick={handleTranslate}
            disabled={translating}
            className="text-[10px] text-(--color-text-muted)/50 hover:text-(--color-text-muted) uppercase tracking-wider transition-colors disabled:opacity-30"
          >
            {translation ? "HIDE" : "TL"}
          </button>
          {timestamp && (
            <span className="text-[10px] text-(--color-text-muted)/30">
              {timestamp}
            </span>
          )}
        </div>
      </div>
      {isUser && (
        <div className="text-[10px] text-(--color-text-muted) pt-1.5 select-none shrink-0 w-7 uppercase">
          you
        </div>
      )}
    </div>
  );
}
