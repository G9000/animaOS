"use client";

import { useState } from "react";
import type { ChatMessage, TraceEvent } from "./types";
import { ChatAvatar } from "./ChatAvatar";
import { CopyButton } from "./CopyButton";
import { RetrievalPanel } from "./RetrievalPanel";
import { TracePanel } from "./TracePanel";
import {
  ThinkIcon,
  TranslateIcon,
  TraceIcon,
  LightbulbIcon,
  ChevronDownIcon,
  XIcon,
} from "./icons";
import {
  formatFullTimestamp,
  getMessageRetrieval,
  formatRetrievalSummary,
  formatTimestamp,
  shouldGroupMessages,
} from "./utils";
import { cn } from "../utils/cn";

export interface ChatBubbleProps {
  message: ChatMessage;
  avatarUrl?: string;
  showTrace?: boolean;
  isGrouped?: boolean;
  onTranslate?: (text: string) => Promise<string>;
  className?: string;
  // Optional: custom renderers
  renderContent?: (content: string, role: string) => React.ReactNode;
}

export function ChatBubble({
  message,
  avatarUrl,
  showTrace = false,
  isGrouped = false,
  onTranslate,
  className,
  renderContent,
}: ChatBubbleProps) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const [translation, setTranslation] = useState<string | null>(null);
  const [translating, setTranslating] = useState(false);
  const [showReasoning, setShowReasoning] = useState(false);
  const [showRetrieval, setShowRetrieval] = useState(false);
  const [showMsgTrace, setShowMsgTrace] = useState(false);
  const [showActions, setShowActions] = useState(false);
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

  const bubbleContent = renderContent ? (
    renderContent(message.content, message.role)
  ) : isUser ? (
    <p className="text-sm whitespace-pre-wrap break-words leading-relaxed pr-6">
      {message.content}
    </p>
  ) : (
    <div className="prose prose-invert prose-sm md:prose-base max-w-none">
      {message.content}
    </div>
  );

  return (
    <div
      className={cn(
        "group flex gap-3 w-full",
        isUser && "flex-row-reverse",
        isGrouped ? "pt-1" : "pt-4",
        className,
      )}
      onMouseEnter={() => setShowActions(true)}
      onMouseLeave={() => setShowActions(false)}
    >
      {/* Avatar */}
      {!isGrouped && (
        <div
          className={cn(
            "flex flex-col items-center shrink-0 w-10",
            isUser ? "items-end" : "items-start",
          )}
        >
          <ChatAvatar
            role={message.role}
            avatarUrl={!isUser ? avatarUrl : undefined}
            size="md"
          />
        </div>
      )}

      {/* Spacer for grouped messages */}
      {isGrouped && <div className="w-10 shrink-0" />}

      {/* Message content */}
      <div
        className={cn(
          "flex flex-col min-w-0",
          isUser ? "items-end" : "items-start",
          "max-w-[85%] md:max-w-[75%] lg:max-w-[65%]",
        )}
      >
        {/* Sender label - only show for first message in group */}
        {!isGrouped && (
          <div className="flex items-center gap-2 mb-1 px-1 select-none">
            <span
              className={cn(
                "font-mono text-[10px] font-medium tracking-wide",
                isUser ? "text-primary/80" : "text-muted-foreground/80",
              )}
            >
              {isUser ? "You" : isSystem ? "System" : "Anima"}
            </span>
            {fullTimestamp && (
              <span
                className="font-mono text-[9px] text-muted-foreground/30"
                title={fullTimestamp}
              >
                {timestamp}
              </span>
            )}
          </div>
        )}

        {/* Message bubble */}
        <div
          className={cn(
            "relative group/bubble",
            isUser
              ? "bg-primary text-primary-foreground"
              : isSystem
                ? "bg-muted/50 border border-border/60"
                : "bg-card border border-border/80 shadow-sm hover:shadow-md transition-shadow",
          )}
        >
          {/* Copy button - appears on hover */}
          <div className="absolute top-2 right-2 opacity-0 group-hover/bubble:opacity-100 transition-opacity">
            <CopyButton text={message.content} />
          </div>

          <div className="px-4 py-2.5">{bubbleContent}</div>
        </div>

        {/* Translation */}
        {translating && (
          <div className="mt-2 px-3 py-2 bg-card/50 border border-border/60 font-mono text-[10px] text-muted-foreground/60 animate-pulse tracking-wider flex items-center gap-2">
            <span className="w-3 h-3 border-2 border-primary/30 border-t-primary/60 animate-spin" />
            Translating...
          </div>
        )}
        {translation && !translating && (
          <div className="mt-2 px-3 py-2 bg-card/50 border border-border/60">
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

        {/* Reasoning */}
        {showReasoning && message.reasoning && (
          <div className="mt-2 w-full px-3 py-3 bg-primary/[0.04] border border-primary/20">
            <div className="flex items-center justify-between mb-2">
              <span className="font-mono text-[9px] text-primary/60 tracking-wider flex items-center gap-1.5">
                <LightbulbIcon className="w-3 h-3" />
                REASONING
              </span>
              <button
                onClick={() => setShowReasoning(false)}
                className="text-muted-foreground/40 hover:text-muted-foreground transition-colors"
              >
                <ChevronDownIcon className="w-3 h-3" />
              </button>
            </div>
            <div className="text-[12px] text-muted-foreground/80 leading-relaxed font-mono whitespace-pre-wrap break-words max-h-60 overflow-y-auto scrollbar-thin">
              {message.reasoning}
            </div>
          </div>
        )}

        {/* Retrieval */}
        {showRetrieval && retrieval && (
          <div className="mt-2 w-full bg-card/50 border border-emerald-400/20 px-3 py-2.5">
            <RetrievalPanel retrieval={retrieval} />
          </div>
        )}

        {/* Trace */}
        {(showTrace || showMsgTrace) && hasTrace && (
          <div className="mt-2 w-full bg-card/50 border border-yellow-400/30 px-3 py-2.5 max-h-80 overflow-y-auto">
            <TracePanel events={message.traceEvents!} />
          </div>
        )}

        {/* Actions bar */}
        <div
          className={cn(
            "flex items-center gap-1 mt-1.5 px-1 transition-all duration-200",
            showActions
              ? "opacity-100 translate-y-0"
              : "opacity-0 -translate-y-1",
          )}
        >
          {!isUser && hasReasoning && (
            <ActionButton
              active={showReasoning}
              onClick={() => setShowReasoning((v) => !v)}
              icon={<ThinkIcon className="w-3 h-3" />}
              label={showReasoning ? "HIDE" : "THINK"}
              activeClass="bg-primary/10 text-primary"
              inactiveClass="text-muted-foreground/50 hover:text-primary hover:bg-primary/5"
            />
          )}

          {!isUser && hasTrace && (
            <ActionButton
              active={showMsgTrace}
              onClick={() => setShowMsgTrace((v) => !v)}
              icon={<TraceIcon className="w-3 h-3" />}
              label={showMsgTrace ? "HIDE" : "TRACE"}
              activeClass="bg-yellow-400/10 text-yellow-400"
              inactiveClass="text-muted-foreground/50 hover:text-yellow-400/70 hover:bg-yellow-400/5"
            />
          )}

          {!isUser && hasRetrieval && (
            <ActionButton
              active={showRetrieval}
              onClick={() => setShowRetrieval((value) => !value)}
              icon={<LightbulbIcon className="w-3 h-3" />}
              label={showRetrieval ? "HIDE" : "CITE"}
              activeClass="bg-emerald-400/10 text-emerald-400"
              inactiveClass="text-muted-foreground/50 hover:text-emerald-400/70 hover:bg-emerald-400/5"
            />
          )}

          <ActionButton
            active={!!translation}
            onClick={handleTranslate}
            disabled={translating}
            icon={<TranslateIcon className="w-3 h-3" />}
            label={translation ? "HIDE" : "TL"}
            activeClass="bg-primary/10 text-primary"
            inactiveClass="text-muted-foreground/50 hover:text-muted-foreground hover:bg-muted-foreground/5"
          />

          {message.source && (
            <span className="font-mono text-[8px] text-muted-foreground/30 ml-1">
              via {message.source}
            </span>
          )}
        </div>

        {/* Token usage - always visible for assistant messages */}
        {!isUser && <TokenUsage events={message.traceEvents} />}
        {!isUser && retrieval && (
          <RetrievalUsage
            retrievalSummary={formatRetrievalSummary(retrieval)}
          />
        )}
      </div>
    </div>
  );
}

// Helper component for action buttons
interface ActionButtonProps {
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  icon: React.ReactNode;
  label: string;
  activeClass: string;
  inactiveClass: string;
}

function ActionButton({
  active,
  onClick,
  disabled,
  icon,
  label,
  activeClass,
  inactiveClass,
}: ActionButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex items-center gap-1 px-2 py-1 font-mono text-[9px] tracking-wider transition-all disabled:opacity-30",
        active ? activeClass : inactiveClass,
      )}
    >
      {icon}
      {label}
    </button>
  );
}

// Token usage display component
interface TokenUsageProps {
  events?: TraceEvent[];
}

function RetrievalUsage({ retrievalSummary }: { retrievalSummary: string }) {
  return (
    <div className="flex items-center gap-2 mt-1 px-1 font-mono text-[9px] text-muted-foreground/30 tracking-wider">
      <span className="text-emerald-400/55">MEMORY</span>
      <span>{retrievalSummary}</span>
    </div>
  );
}

function TokenUsage({ events }: TokenUsageProps) {
  const usage = events?.find((e) => e.type === "usage");
  const timing = events?.filter((e) => e.type === "timing");

  if (!usage) return null;

  const totalMs =
    timing?.reduce((sum, t) => sum + (t.stepDurationMs ?? 0), 0) ?? 0;
  const steps =
    events?.filter((e) => e.type === "step_state" && e.phase === "request")
      .length ?? 0;

  return (
    <div className="flex items-center gap-2 mt-1 px-1 font-mono text-[9px] text-muted-foreground/30">
      <span>{(usage.totalTokens ?? 0).toLocaleString()} tkn</span>
      <span className="text-muted-foreground/15">·</span>
      <span>{usage.promptTokens ?? 0}in</span>
      <span>{usage.completionTokens ?? 0}out</span>
      {(usage.cachedInputTokens ?? 0) > 0 && (
        <span className="text-emerald-500/40">
          {usage.cachedInputTokens}cached
        </span>
      )}
      {(usage.reasoningTokens ?? 0) > 0 && (
        <span className="text-purple-400/40">
          {usage.reasoningTokens}reason
        </span>
      )}
      {totalMs > 0 && (
        <>
          <span className="text-muted-foreground/15">·</span>
          <span>{(totalMs / 1000).toFixed(1)}s</span>
        </>
      )}
      {steps > 1 && (
        <span className="text-muted-foreground/15">{steps} steps</span>
      )}
    </div>
  );
}

export { shouldGroupMessages };
export type { ChatMessage, TraceEvent };
