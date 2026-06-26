import { useState, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import { TracePanel } from "@anima/standard-templates";
import type { TraceEvent } from "@anima/api-client";

interface StreamingViewProps {
  streaming: boolean;
  streamBuffer: string;
  reasoningBuffer: string;
  traceEvents: TraceEvent[];
  showTrace: boolean;
  thinkingMonologue: string[];
}

const DEFAULT_THINKING_MONOLOGUE = [
  "one sec",
  "checking this",
  "looking this over",
  "working on it",
  "almost there",
];

const DOTS = [0, 120, 240];

function ThinkingAnimation({ thinkingMonologue }: { thinkingMonologue: string[] }) {
  const lines = thinkingMonologue.length ? thinkingMonologue : DEFAULT_THINKING_MONOLOGUE;
  const [idx, setIdx] = useState(() => Math.floor(Math.random() * lines.length));
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    setIdx((current) => current % lines.length);
  }, [lines.length]);

  useEffect(() => {
    const timer = setInterval(() => {
      setVisible(false);
      setTimeout(() => {
        setIdx(() => Math.floor(Math.random() * lines.length));
        setVisible(true);
      }, 250);
    }, 1800);
    return () => clearInterval(timer);
  }, [lines.length]);

  return (
    <div className="flex items-center gap-3 py-1 px-1">
      <div className="flex gap-[5px] items-end h-4">
        {DOTS.map((delay) => (
          <span
            key={delay}
            className="w-[5px] h-[5px] rounded-full bg-foreground/30"
            style={{ animation: "thinking-dot 1.4s ease-in-out infinite", animationDelay: `${delay}ms` }}
          />
        ))}
      </div>
      <span
        className="font-mono text-[10px] text-foreground/30 transition-opacity duration-300"
        style={{ opacity: visible ? 1 : 0 }}
      >
        {lines[idx % lines.length]}
      </span>
    </div>
  );
}

export function StreamingView({
  streaming,
  streamBuffer,
  reasoningBuffer,
  traceEvents,
  showTrace,
  thinkingMonologue,
}: StreamingViewProps) {
  if (!streaming) return null;

  return (
    <>
      {/* Live trace panel during streaming */}
      {showTrace && traceEvents.length > 0 && (
        <div className="animate-in fade-in duration-200 pt-2">
          <div className="font-mono text-[8px] text-yellow-400/60 tracking-[0.2em] uppercase mb-2">
            TRACE
          </div>
          <div className="bg-card/50 border-l-2 border-yellow-400/40 px-4 py-2.5">
            <TracePanel events={traceEvents} />
          </div>
        </div>
      )}

      {/* Reasoning indicator */}
      {reasoningBuffer && (
        <div className="animate-in fade-in duration-200 pt-2">
          <div className="font-mono text-[8px] text-accent/50 tracking-[0.2em] uppercase mb-1.5">
            thinking
          </div>
          <div className="bg-primary/[0.06] border border-primary/15 px-4 py-3">
            <div className="text-[12px] text-muted-foreground/70 whitespace-pre-wrap break-words leading-relaxed font-mono">
              {reasoningBuffer}
              <span className="inline-block w-1.5 h-3 bg-primary/50 ml-0.5 animate-cursor" />
            </div>
          </div>
        </div>
      )}

      {/* Streaming content */}
      {streamBuffer && (
        <div className="animate-in fade-in duration-200 pt-2">
          <div className="bg-card border border-border/80 px-4 py-3">
            <div className="prose prose-invert prose-sm md:prose-base max-w-none">
              <ReactMarkdown rehypePlugins={[rehypeHighlight]}>
                {streamBuffer}
              </ReactMarkdown>
              <span className="inline-block w-1.5 h-4 bg-primary/70 ml-0.5 animate-cursor" />
            </div>
          </div>
        </div>
      )}

      {/* Waiting — thinking animation */}
      {!streamBuffer && !reasoningBuffer && (
        <div className="animate-in fade-in duration-200 pt-2">
          <div className="w-fit bg-background/25 backdrop-blur-[40px] border border-foreground/[0.08] px-4 py-3">
            <ThinkingAnimation thinkingMonologue={thinkingMonologue} />
          </div>
        </div>
      )}
    </>
  );
}
