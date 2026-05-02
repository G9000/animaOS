import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import { ChatAvatar, TracePanel } from "@anima/standard-templates";
import type { TraceEvent } from "@anima/api-client";

interface StreamingViewProps {
  streaming: boolean;
  streamBuffer: string;
  reasoningBuffer: string;
  traceEvents: TraceEvent[];
  showTrace: boolean;
  agentAvatarUrl: string;
}

export function StreamingView({
  streaming,
  streamBuffer,
  reasoningBuffer,
  traceEvents,
  showTrace,
  agentAvatarUrl,
}: StreamingViewProps) {
  if (!streaming) return null;

  return (
    <>
      {/* Live trace panel during streaming */}
      {showTrace && traceEvents.length > 0 && (
        <div className="flex gap-3 animate-in fade-in duration-200 pt-2">
          <div className="font-mono text-[9px] text-yellow-400/70 pt-2.5 select-none shrink-0 w-12 text-right tracking-wider">
            TRACE
          </div>
          <div className="max-w-[84%] md:max-w-[72%] xl:max-w-[62%] w-full bg-card/50 border-l-2 border-yellow-400/40 px-4 py-2.5">
            <TracePanel events={traceEvents} />
          </div>
        </div>
      )}

      {/* Reasoning indicator */}
      {reasoningBuffer && (
        <div className="flex gap-3 animate-in fade-in duration-200 pt-2">
          <div className="flex flex-col items-center shrink-0 w-12 pt-2">
            <ChatAvatar role="assistant" avatarUrl={agentAvatarUrl} size="md" />
            <span className="font-mono text-[9px] text-primary/65 select-none tracking-wider">
              THINK
            </span>
          </div>
          <div className="max-w-[84%] md:max-w-[72%] xl:max-w-[62%] bg-primary/[0.06] border border-primary/20 px-4 py-3">
            <div className="text-[12px] text-muted-foreground/80 whitespace-pre-wrap break-words leading-relaxed font-mono">
              {reasoningBuffer}
              <span className="inline-block w-1.5 h-3 bg-primary/60 ml-0.5 animate-cursor" />
            </div>
          </div>
        </div>
      )}

      {/* Streaming content */}
      {streamBuffer && (
        <div className="flex gap-3 animate-in fade-in duration-200 pt-2">
          <div className="flex flex-col items-center shrink-0 w-12 pt-2">
            <ChatAvatar role="assistant" avatarUrl={agentAvatarUrl} size="md" />
          </div>
          <div className="max-w-[84%] md:max-w-[72%] xl:max-w-[62%] bg-card border border-border/80 px-4 py-3 ">
            <div className="prose prose-invert prose-sm md:prose-base max-w-none">
              <ReactMarkdown rehypePlugins={[rehypeHighlight]}>
                {streamBuffer}
              </ReactMarkdown>
              <span className="inline-block w-1.5 h-4 bg-primary/70 ml-0.5 animate-cursor" />
            </div>
          </div>
        </div>
      )}

      {/* Waiting indicator */}
      {!streamBuffer && !reasoningBuffer && (
        <div className="flex gap-3 animate-in fade-in duration-200 pt-2">
          <div className="flex flex-col items-center shrink-0 w-12 pt-2">
            <ChatAvatar role="assistant" avatarUrl={agentAvatarUrl} size="md" />
          </div>
          <div className="max-w-[84%] md:max-w-[72%] xl:max-w-[62%] bg-card/60 border border-border/60 px-4 py-3">
            <div className="flex gap-1.5 items-center h-5 font-mono text-[10px] text-muted-foreground/70 tracking-wider">
              <span className="animate-pulse">PROCESSING</span>
              <span className="w-1.5 h-3 bg-primary/40 animate-cursor" />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
