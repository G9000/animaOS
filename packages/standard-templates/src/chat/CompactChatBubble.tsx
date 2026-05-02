"use client";

import { useState } from "react";
import type { ChatMessage, TraceEvent } from "./types";
import { CopyButton } from "./CopyButton";
import { RetrievalPanel } from "./RetrievalPanel";
import { TracePanel } from "./TracePanel";
import {
  ThinkIcon,
  TranslateIcon,
  TraceIcon,
  LightbulbIcon,
  XIcon,
} from "./icons";
import {
  formatFullTimestamp,
  getMessageRetrieval,
  formatRetrievalSummary,
  formatTimestamp,
} from "./utils";
import { cn } from "../utils/cn";

export interface CompactChatBubbleProps {
  message: ChatMessage;
  avatarUrl?: string;
  showTrace?: boolean;
  isGrouped?: boolean;
  onTranslate?: (text: string) => Promise<string>;
  className?: string;
  renderContent?: (content: string, role: string) => React.ReactNode;
}

export function CompactChatBubble({
  message,
  avatarUrl,
  showTrace = false,
  isGrouped = false,
  onTranslate,
  className,
  renderContent,
}: CompactChatBubbleProps) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const [translation, setTranslation] = useState<string | null>(null);
  const [translating, setTranslating] = useState(false);
  const [showReasoning, setShowReasoning] = useState(false);
  const [showRetrieval, setShowRetrieval] = useState(false);
  const [showMsgTrace, setShowMsgTrace] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const hasTrace = message.traceEvents && message.traceEvents.length > 0;
  const hasReasoning = !!message.reasoning;
  const retrieval = getMessageRetrieval(message);
  const hasRetrieval = retrieval != null;

  const handleTranslate = async () => {
    if (translating) return;
    if (translation) {
      setTranslation(null);
      return;
    }
    if (!onTranslate) {
      setTranslation("[translation not configured]");
      return;
    }
    setTranslating(true);
    try {
      const result = await onTranslate(message.content);
      setTranslation(result);
    } catch {
      setTranslation("[translation failed]");
    } finally {
      setTranslating(false);
    }
  };

  const timestamp = formatTimestamp(message.createdAt);
  const fullTimestamp = formatFullTimestamp(message.createdAt);
  const displayName = isUser ? "You" : isSystem ? "System" : "Anima";

  const bubbleContent = renderContent ? (
    renderContent(message.content, message.role)
  ) : (
    <p className="text-sm whitespace-pre-wrap break-words leading-relaxed">
      {message.content}
    </p>
  );

  // Show panels if hovered AND toggled on
  const showReasoningPanel = isHovered && showReasoning && message.reasoning;
  const showRetrievalPanel = isHovered && showRetrieval && retrieval;
  const showTracePanel = isHovered && (showTrace || showMsgTrace) && hasTrace;
  const showTranslationPanel = isHovered && translation && !translating;
  const showTranslatingIndicator = isHovered && translating;

  // Any panel is showing
  const hasPanelOpen =
    showReasoningPanel ||
    showRetrievalPanel ||
    showTracePanel ||
    showTranslationPanel ||
    showTranslatingIndicator;

  return (
    <div
      className={cn(
        "group flex w-full",
        isUser ? "justify-end" : "justify-start",
        isGrouped ? "pt-1" : "pt-4",
        className,
      )}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div
        className={cn(
          "flex flex-col max-w-[90%] md:max-w-[80%] lg:max-w-[70%]",
          isUser ? "items-end" : "items-start",
        )}
      >
        {/* Main Bubble */}
        <div
          className={cn(
            "relative flex flex-col min-w-[200px]",
            isUser
              ? "bg-primary text-primary-foreground"
              : isSystem
                ? "bg-muted/50 border border-border/60"
                : "bg-card border border-border/80 hover:transition-shadow",
          )}
        >
          {/* Header Row */}
          <div
            className={cn(
              "flex items-center gap-2 px-3 py-2 border-b",
              isUser
                ? "border-primary-foreground/20 flex-row-reverse"
                : "border-border/40",
            )}
          >
            {/* Avatar */}
            {isUser ? (
              <div className="w-6 h-6 bg-primary-foreground/20 flex items-center justify-center">
                <svg
                  className="w-3.5 h-3.5 text-primary-foreground/80"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"
                  />
                </svg>
              </div>
            ) : avatarUrl ? (
              <img
                src={avatarUrl}
                alt="Agent"
                className="w-6 h-6 object-cover ring-1 ring-border/50"
              />
            ) : (
              <div className="w-6 h-6 bg-primary/10 flex items-center justify-center">
                <span className="font-mono text-[8px] text-primary/60">AI</span>
              </div>
            )}

            {/* Name */}
            <span
              className={cn(
                "font-mono text-[10px] font-medium tracking-wide",
                isUser ? "text-primary-foreground/90" : "text-foreground/80",
              )}
            >
              {displayName}
            </span>

            {/* Timestamp - pushed to opposite side */}
            {fullTimestamp && (
              <span
                className={cn(
                  "font-mono text-[9px]",
                  isUser ? "mr-auto" : "ml-auto",
                  isUser
                    ? "text-primary-foreground/50"
                    : "text-muted-foreground/40",
                )}
                title={fullTimestamp}
              >
                {timestamp}
              </span>
            )}

            {/* Copy button - appears on hover */}
            <div className="opacity-0 group-hover:opacity-100 transition-opacity">
              <CopyButton
                text={message.content}
                className={cn(
                  isUser
                    ? "text-primary-foreground/50 hover:text-primary-foreground hover:bg-primary-foreground/10"
                    : "",
                )}
              />
            </div>
          </div>

          {/* Message Content */}
          <div className="px-3 py-2.5">{bubbleContent}</div>
        </div>

        {/* UTILITY BAR - Below bubble, aligned same as bubble */}
        <div
          className={cn(
            "flex items-center gap-3 mt-1 transition-all duration-200",
            isHovered ? "opacity-100" : "opacity-0",
            isUser ? "flex-row-reverse" : "flex-row",
          )}
        >
          {/* Action buttons */}
          <div
            className={cn(
              "flex items-center gap-1",
              isUser ? "flex-row-reverse" : "flex-row",
            )}
          >
            {hasReasoning && (
              <FloatyButton
                active={showReasoning}
                onClick={() => setShowReasoning((v) => !v)}
                icon={<ThinkIcon className="w-3.5 h-3.5" />}
                label="THINK"
              />
            )}

            {hasTrace && (
              <FloatyButton
                active={showMsgTrace}
                onClick={() => setShowMsgTrace((v) => !v)}
                icon={<TraceIcon className="w-3.5 h-3.5" />}
                label="TRACE"
              />
            )}

            {hasRetrieval && (
              <FloatyButton
                active={showRetrieval}
                onClick={() => setShowRetrieval((value) => !value)}
                icon={<LightbulbIcon className="w-3.5 h-3.5" />}
                label="CITE"
              />
            )}

            <FloatyButton
              active={!!translation}
              onClick={handleTranslate}
              disabled={translating}
              icon={<TranslateIcon className="w-3.5 h-3.5" />}
              label="TL"
            />
          </div>

          {/* Token usage (assistant only) */}
          {!isUser && <CompactTokenUsage events={message.traceEvents} />}
        </div>

        {!isUser && retrieval && (
          <div className="mt-1 px-1 font-mono text-[9px] tracking-wider text-muted-foreground/30">
            <span className="text-emerald-400/55">MEMORY</span>{" "}
            <span>{formatRetrievalSummary(retrieval)}</span>
          </div>
        )}

        {/* EXTERNAL PANELS - Aligned same as bubble, same width as bubble */}
        {hasPanelOpen && (
          <div
            className={cn(
              "w-full mt-1 space-y-1",
              isUser ? "text-right" : "text-left",
            )}
          >
            {/* Reasoning Panel */}
            {showReasoningPanel && (
              <div
                className={cn(
                  "inline-block text-left px-3 py-2 border",
                  "bg-primary/[0.04] border-primary/20",
                  "min-w-[200px] max-w-full",
                )}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-mono text-[9px] text-primary/60 tracking-wider flex items-center gap-1">
                    <LightbulbIcon className="w-3 h-3" />
                    REASONING
                  </span>
                  <button
                    onClick={() => setShowReasoning(false)}
                    className="text-muted-foreground/40 hover:text-muted-foreground transition-colors"
                  >
                    <XIcon className="w-3 h-3" />
                  </button>
                </div>
                <div className="text-[11px] text-muted-foreground/80 leading-relaxed font-mono whitespace-pre-wrap break-words max-h-48 overflow-y-auto text-left">
                  {message.reasoning}
                </div>
              </div>
            )}

            {/* Retrieval Panel */}
            {showRetrievalPanel && retrieval && (
              <div
                className={cn(
                  "inline-block text-left px-3 py-2 border rounded-none",
                  "bg-card/50 border-emerald-400/25",
                  "min-w-[200px] max-w-full",
                )}
              >
                <div className="flex items-center justify-between mb-1.5">
                  <span className="font-mono text-[9px] text-emerald-400/60 tracking-wider flex items-center gap-1">
                    <LightbulbIcon className="w-3 h-3" />
                    MEMORY HITS
                  </span>
                  <button
                    onClick={() => setShowRetrieval(false)}
                    className="text-muted-foreground/40 hover:text-muted-foreground transition-colors"
                  >
                    <XIcon className="w-3 h-3" />
                  </button>
                </div>
                <RetrievalPanel retrieval={retrieval} />
              </div>
            )}

            {/* Trace Panel */}
            {showTracePanel && (
              <div
                className={cn(
                  "inline-block text-left px-3 py-2 border rounded-none max-h-64 overflow-y-auto",
                  "bg-card/50 border-yellow-400/30",
                  "min-w-[200px] max-w-full",
                )}
              >
                <TracePanel events={message.traceEvents!} />
              </div>
            )}

            {/* Translation Panel */}
            {showTranslationPanel && (
              <div
                className={cn(
                  "inline-block text-left px-3 py-2 border rounded-none",
                  "bg-card/50 border-border/60",
                  "min-w-[200px] max-w-full",
                )}
              >
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-mono text-[9px] text-primary/60 tracking-wider">
                    TRANSLATION
                  </span>
                  <button
                    onClick={() => setTranslation(null)}
                    className="text-muted-foreground/40 hover:text-muted-foreground transition-colors"
                  >
                    <XIcon className="w-3 h-3" />
                  </button>
                </div>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {translation}
                </p>
              </div>
            )}

            {/* Translating indicator */}
            {showTranslatingIndicator && (
              <div className="inline-block px-3 py-2 bg-card/50 border rounded-none border-border/60 flex items-center gap-2">
                <span className="w-3 h-3 border border-primary/30 border-t-primary/60 animate-spin" />
                <span className="font-mono text-[9px] text-muted-foreground/60 tracking-wider">
                  Translating...
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// Floaty button - no background, just text/icon
interface FloatyButtonProps {
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  icon: React.ReactNode;
  label: string;
}

function FloatyButton({
  active,
  onClick,
  disabled,
  icon,
  label,
}: FloatyButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex items-center gap-1.5 px-2 py-1 font-mono text-[9px] tracking-wider transition-all disabled:opacity-30",
        active
          ? "text-primary"
          : "text-muted-foreground/40 hover:text-muted-foreground",
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

// Compact token usage component
interface CompactTokenUsageProps {
  events?: TraceEvent[];
}

function CompactTokenUsage({ events }: CompactTokenUsageProps) {
  const usage = events?.find((e) => e.type === "usage");
  const timing = events?.filter((e) => e.type === "timing");

  if (!usage) return null;

  const totalMs =
    timing?.reduce((sum, t) => sum + (t.stepDurationMs ?? 0), 0) ?? 0;

  return (
    <div className="flex items-center gap-1.5 font-mono text-[8px] text-muted-foreground/40">
      <span>{(usage.totalTokens ?? 0).toLocaleString()}</span>
      {(usage.cachedInputTokens ?? 0) > 0 && (
        <span className="text-emerald-500/50">
          · {usage.cachedInputTokens}cached
        </span>
      )}
      {totalMs > 0 && (
        <span className="text-muted-foreground/30">
          · {(totalMs / 1000).toFixed(1)}s
        </span>
      )}
    </div>
  );
}
