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
  renderContent?: (
    content: string,
    role: string,
    message: ChatMessage,
  ) => React.ReactNode;
}

// Subtle scan-line texture — HUD feel
const scanLines = "bg-[repeating-linear-gradient(180deg,transparent_0px,transparent_3px,rgba(255,255,255,0.012)_3px,rgba(255,255,255,0.012)_4px)]";

export function CompactChatBubble({
  message,
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
    if (translation) { setTranslation(null); return; }
    if (!onTranslate) { setTranslation("[translation not configured]"); return; }
    setTranslating(true);
    try {
      const result = await onTranslate(message.content);
      setTranslation(result?.trim() || "[translation unavailable]");
    } catch {
      setTranslation("[translation failed]");
    } finally {
      setTranslating(false);
    }
  };

  const timestamp = formatTimestamp(message.createdAt);
  const fullTimestamp = formatFullTimestamp(message.createdAt);

  const bubbleContent = renderContent ? (
    renderContent(message.content, message.role, message)
  ) : (
    <p className="text-sm whitespace-pre-wrap break-words leading-relaxed">
      {message.content}
    </p>
  );

  const showReasoningPanel = showReasoning && !!message.reasoning;
  const showRetrievalPanel = showRetrieval && retrieval != null;
  const showTracePanel = (showTrace || showMsgTrace) && hasTrace;
  const hasPanelOpen =
    showReasoningPanel ||
    showRetrievalPanel ||
    showTracePanel ||
    translation !== null ||
    translating;

  // ── SYSTEM ───────────────────────────────────────────────────────────────
  if (isSystem) {
    return (
      <div className={cn("flex justify-center", isGrouped ? "pt-1" : "pt-6", className)}>
        <div className="max-w-[80%] px-3 py-2 bg-background/25 backdrop-blur-[32px] shadow-[0_4px_20px_rgba(0,0,0,0.25)]">
          <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-foreground/30 select-none block mb-1">
            System
          </span>
          {bubbleContent}
        </div>
      </div>
    );
  }

  // ── USER ─────────────────────────────────────────────────────────────────
  if (isUser) {
    return (
      <div
        className={cn("flex justify-end", isGrouped ? "pt-1" : "pt-6", className)}
        onMouseEnter={() => setIsHovered(true)}
        onMouseLeave={() => setIsHovered(false)}
      >
        <div className="flex flex-col items-end max-w-[65%] lg:max-w-[55%]">
          {/* Header chip */}
          {!isGrouped && fullTimestamp && (
            <div className="inline-flex bg-background/20 backdrop-blur-[16px] px-2.5 py-1 mb-2">
              <span className="font-mono text-[9px] text-foreground/30 select-none" title={fullTimestamp}>
                {timestamp}
              </span>
            </div>
          )}

          {/* Content — identical glass panel as assistant */}
          <div className="relative bg-background/[0.28] backdrop-blur-[40px] shadow-[0_4px_28px_rgba(0,0,0,0.30)] w-full overflow-hidden">
            <div className={cn("absolute inset-0 pointer-events-none opacity-50", scanLines)} />
            <div className="relative px-4 py-3.5">
              {bubbleContent}
            </div>
          </div>

          {/* Panels */}
          {hasPanelOpen && (
            <div className="w-full mt-1 space-y-px">
              {translating && <TranslatingIndicator />}
              {translation != null && !translating && (
                <TranslationPanel translation={translation} onClose={() => setTranslation(null)} />
              )}
            </div>
          )}

          {/* Utility bar */}
          <div
            className={cn(
              "inline-flex items-center gap-0.5 mt-1 bg-background/20 backdrop-blur-[20px] shadow-[0_2px_10px_rgba(0,0,0,0.20)] px-1.5 py-0.5 transition-all duration-150",
              isHovered ? "opacity-100" : "opacity-0 pointer-events-none",
            )}
          >
            <BarButton
              active={!!translation}
              onClick={handleTranslate}
              disabled={translating}
              icon={<TranslateIcon className="w-3 h-3" />}
              label="TL"
            />
            <div className="text-foreground/25 hover:text-foreground/55 transition-colors">
              <CopyButton text={message.content} />
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ── ASSISTANT ─────────────────────────────────────────────────────────────
  return (
    <div
      className={cn("w-full", isGrouped ? "pt-1" : "pt-7", className)}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {/* Header — timestamp only */}
      {!isGrouped && fullTimestamp && (
        <div className="inline-flex bg-background/20 backdrop-blur-[16px] px-2.5 py-1 mb-2">
          <span className="font-mono text-[9px] text-foreground/30 select-none" title={fullTimestamp}>
            {timestamp}
          </span>
        </div>
      )}

      {/* Content — scan lines */}
      <div className="relative bg-background/[0.28] backdrop-blur-[40px] shadow-[0_4px_28px_rgba(0,0,0,0.30)] px-4 py-3.5 text-foreground/90 overflow-hidden">
        <div className={cn("absolute inset-0 pointer-events-none opacity-50", scanLines)} />
        <div className="relative">{bubbleContent}</div>
      </div>

      {/* Panels — left-accent strip, no full rectangle */}
      {hasPanelOpen && (
        <div className="mt-1 space-y-px">
          {showReasoningPanel && (
            <div className="pl-3 pr-3 py-2.5 bg-background/[0.22] backdrop-blur-[28px] border-l-2 border-accent/35 shadow-[0_2px_12px_rgba(0,0,0,0.18)]">
              <div className="flex items-center justify-between mb-2">
                <span className="font-mono text-[9px] text-accent/50 tracking-[0.15em] uppercase flex items-center gap-1.5">
                  <LightbulbIcon className="w-3 h-3" />
                  Reasoning
                </span>
                <button onClick={() => setShowReasoning(false)} className="text-foreground/25 hover:text-foreground/60 transition-colors">
                  <XIcon className="w-3 h-3" />
                </button>
              </div>
              <div className="text-[11px] text-foreground/55 leading-relaxed font-mono whitespace-pre-wrap break-words max-h-48 overflow-y-auto">
                {message.reasoning}
              </div>
            </div>
          )}

          {showRetrievalPanel && retrieval && (
            <div className="pl-3 pr-3 py-2.5 bg-background/[0.22] backdrop-blur-[28px] border-l-2 border-emerald-400/40 shadow-[0_2px_12px_rgba(0,0,0,0.18)]">
              <div className="flex items-center justify-between mb-1.5">
                <span className="font-mono text-[9px] text-emerald-400/55 tracking-[0.15em] uppercase flex items-center gap-1.5">
                  <LightbulbIcon className="w-3 h-3" />
                  Memory
                </span>
                <button onClick={() => setShowRetrieval(false)} className="text-foreground/25 hover:text-foreground/60 transition-colors">
                  <XIcon className="w-3 h-3" />
                </button>
              </div>
              <RetrievalPanel retrieval={retrieval} />
            </div>
          )}

          {showTracePanel && (
            <div className="pl-3 pr-3 py-2.5 bg-background/[0.22] backdrop-blur-[28px] border-l-2 border-accent/30 shadow-[0_2px_12px_rgba(0,0,0,0.18)] max-h-64 overflow-y-auto">
              <TracePanel events={message.traceEvents!} />
            </div>
          )}

          {translating && <TranslatingIndicator />}
          {translation != null && !translating && (
            <TranslationPanel translation={translation} onClose={() => setTranslation(null)} />
          )}
        </div>
      )}

      {/* Utility bar */}
      <div
        className={cn(
          "inline-flex items-center gap-0.5 mt-1 bg-background/20 backdrop-blur-[20px] shadow-[0_2px_10px_rgba(0,0,0,0.18)] px-1.5 py-0.5 transition-all duration-150",
          isHovered ? "opacity-100" : "opacity-0 pointer-events-none",
        )}
      >
        {hasReasoning && (
          <BarButton active={showReasoning} onClick={() => setShowReasoning((v) => !v)} icon={<ThinkIcon className="w-3 h-3" />} label="Think" />
        )}
        {hasTrace && (
          <BarButton active={showMsgTrace} onClick={() => setShowMsgTrace((v) => !v)} icon={<TraceIcon className="w-3 h-3" />} label="Trace" />
        )}
        {hasRetrieval && (
          <BarButton active={showRetrieval} onClick={() => setShowRetrieval((v) => !v)} icon={<LightbulbIcon className="w-3 h-3" />} label="Cite" />
        )}
        <BarButton active={!!translation} onClick={handleTranslate} disabled={translating} icon={<TranslateIcon className="w-3 h-3" />} label="TL" />
        <div className="text-foreground/25 hover:text-foreground/55 transition-colors">
          <CopyButton text={message.content} />
        </div>
        <CompactTokenUsage events={message.traceEvents} />
        {hasRetrieval && retrieval && (
          <span className="font-mono text-[8px] text-emerald-400/40 ml-1">
            · mem {formatRetrievalSummary(retrieval)}
          </span>
        )}
      </div>
    </div>
  );
}

// ── Shared sub-components ──────────────────────────────────────────────────

function TranslatingIndicator() {
  return (
    <div className="pl-3 pr-3 py-2 bg-background/[0.22] backdrop-blur-[28px] border-l-2 border-accent/30 shadow-[0_2px_12px_rgba(0,0,0,0.15)] font-mono text-[9px] text-foreground/35 tracking-[0.15em] uppercase flex items-center gap-2">
      <span className="w-2.5 h-2.5 border border-accent/30 border-t-accent/70 animate-spin shrink-0" />
      Translating...
    </div>
  );
}

function TranslationPanel({ translation, onClose }: { translation: string; onClose: () => void }) {
  return (
    <div className="pl-3 pr-3 py-2.5 bg-background/[0.22] backdrop-blur-[28px] border-l-2 border-foreground/20 shadow-[0_2px_12px_rgba(0,0,0,0.15)]">
      <div className="flex items-center justify-between mb-1.5">
        <span className="font-mono text-[9px] text-foreground/35 tracking-[0.15em] uppercase">Translation</span>
        <button onClick={onClose} className="text-foreground/25 hover:text-foreground/60 transition-colors">
          <XIcon className="w-3 h-3" />
        </button>
      </div>
      <p className="text-sm text-foreground/65 leading-relaxed">{translation}</p>
    </div>
  );
}

interface BarButtonProps {
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  icon: React.ReactNode;
  label: string;
}

function BarButton({ active, onClick, disabled, icon, label }: BarButtonProps) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "flex items-center gap-1 px-1.5 py-1 font-mono text-[9px] tracking-[0.1em] transition-all duration-150 disabled:opacity-30",
        active ? "text-accent/80" : "text-foreground/30 hover:text-foreground/60",
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}

function CompactTokenUsage({ events }: { events?: TraceEvent[] }) {
  const usage = events?.find((e) => e.type === "usage");
  const timing = events?.filter((e) => e.type === "timing");
  if (!usage) return null;
  const totalMs = timing?.reduce((sum, t) => sum + (t.stepDurationMs ?? 0), 0) ?? 0;
  return (
    <div className="flex items-center gap-1 font-mono text-[8px] text-foreground/20 ml-1">
      <span>{(usage.totalTokens ?? 0).toLocaleString()} tkn</span>
      {(usage.cachedInputTokens ?? 0) > 0 && (
        <span className="text-emerald-500/35">· {usage.cachedInputTokens}c</span>
      )}
      {totalMs > 0 && <span>· {(totalMs / 1000).toFixed(1)}s</span>}
    </div>
  );
}
