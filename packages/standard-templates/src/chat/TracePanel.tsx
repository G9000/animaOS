"use client";

import { useState } from "react";
import type { TraceEvent } from "./types";
import { formatJson, serializeTraceAsJson, serializeTraceAsText } from "./utils";

export interface TracePanelProps {
  events: TraceEvent[];
}

export function TracePanel({ events }: TracePanelProps) {
  const [copyState, setCopyState] = useState<"json" | "text" | null>(null);

  const handleCopy = async (mode: "json" | "text") => {
    const payload =
      mode === "json"
        ? serializeTraceAsJson(events)
        : serializeTraceAsText(events);
    await navigator.clipboard.writeText(payload);
    setCopyState(mode);
    window.setTimeout(
      () => setCopyState((current) => (current === mode ? null : current)),
      1200,
    );
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
          className="flex items-center gap-1.5 text-left w-full hover:bg-input/30 px-1 py-0.5 -mx-1 transition-colors"
        >
          <span className="text-cyan-400/70 text-[9px]">STEP</span>
          <span className="text-muted-foreground/70">
            #{event.stepIndex ?? 0}
          </span>
          <span className="text-muted-foreground">
            {isRequest ? "request" : "result"}
          </span>
          <span className="text-muted-foreground/45">{summary}</span>
          <span className="text-muted-foreground/30 text-[9px] ml-auto">
            {expanded ? "▼" : "▶"}
          </span>
        </button>
        {expanded && (
          <pre className="text-[10px] text-muted-foreground/50 bg-input/20 px-2 py-1.5 mt-0.5 overflow-x-auto max-h-48 whitespace-pre-wrap break-words">
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
          <span className="text-muted-foreground/55">{event.message}</span>
        )}
      </div>
    );
  }

  if (event.type === "tool_call") {
    return (
      <div className="font-mono text-[11px]">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 text-left w-full hover:bg-input/30 px-1 py-0.5 -mx-1 transition-colors"
        >
          <span className="text-yellow-500/70 text-[9px]">CALL</span>
          <span className="text-muted-foreground">{event.name}</span>
          <span className="text-muted-foreground/30 text-[9px] ml-auto">
            {expanded ? "▼" : "▶"}
          </span>
        </button>
        {expanded && event.arguments != null && (
          <pre className="text-[10px] text-muted-foreground/50 bg-input/20 px-2 py-1.5 mt-0.5 overflow-x-auto max-h-40 whitespace-pre-wrap break-words">
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
          className="flex items-center gap-1.5 text-left w-full hover:bg-input/30 px-1 py-0.5 -mx-1 transition-colors"
        >
          <span
            className={`text-[9px] ${event.isError ? "text-destructive" : "text-emerald-500/70"}`}
          >
            {event.isError ? "ERR" : "RET"}
          </span>
          <span className="text-muted-foreground">{event.name}</span>
          <span className="text-muted-foreground/30 text-[9px] ml-auto">
            {expanded ? "▼" : "▶"}
          </span>
        </button>
        {expanded && event.output && (
          <pre className="text-[10px] text-muted-foreground/50 bg-input/20 px-2 py-1.5 mt-0.5 overflow-x-auto max-h-40 whitespace-pre-wrap break-words">
            {event.output}
          </pre>
        )}
      </div>
    );
  }

  if (event.type === "usage") {
    return (
      <div className="font-mono text-[10px] text-muted-foreground/40 flex items-center gap-2 px-1 py-0.5">
        <span className="text-blue-400/60 text-[9px]">TOKENS</span>
        <span>{event.promptTokens ?? 0}in</span>
        <span>{event.completionTokens ?? 0}out</span>
        {event.reasoningTokens ? (
          <span>{event.reasoningTokens}reason</span>
        ) : null}
        {event.cachedInputTokens ? (
          <span>{event.cachedInputTokens}cached</span>
        ) : null}
        <span className="text-muted-foreground/25">
          = {event.totalTokens ?? 0}
        </span>
      </div>
    );
  }

  if (event.type === "timing") {
    return (
      <div className="font-mono text-[10px] text-muted-foreground/40 flex items-center gap-2 px-1 py-0.5">
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
      <div className="font-mono text-[10px] text-muted-foreground/40 flex items-center gap-2 px-1 py-0.5">
        <span className="text-emerald-500/60 text-[9px]">DONE</span>
        {event.provider && <span>{event.provider}</span>}
        {event.model && (
          <span className="text-muted-foreground/25">{event.model}</span>
        )}
        {event.toolsUsed && event.toolsUsed.length > 0 && (
          <span className="text-yellow-500/40">
            tools:[{event.toolsUsed.join(",")}]
          </span>
        )}
        {event.stopReason && (
          <span className="text-muted-foreground/25">
            stop:{event.stopReason}
          </span>
        )}
      </div>
    );
  }

  if (event.type === "approval_pending") {
    return (
      <div className="font-mono text-[11px]">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-1.5 text-left w-full hover:bg-input/30 px-1 py-0.5 -mx-1 transition-colors"
        >
          <span className="text-orange-400/70 text-[9px]">WAIT</span>
          <span className="text-muted-foreground">{event.name}</span>
          {event.runId != null && (
            <span className="text-muted-foreground/30">run:{event.runId}</span>
          )}
          <span className="text-muted-foreground/30 text-[9px] ml-auto">
            {expanded ? "▼" : "▶"}
          </span>
        </button>
        {expanded && (
          <pre className="text-[10px] text-muted-foreground/50 bg-input/20 px-2 py-1.5 mt-0.5 overflow-x-auto max-h-40 whitespace-pre-wrap break-words">
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
      <div className="font-mono text-[10px] text-muted-foreground/40 flex items-center gap-2 px-1 py-0.5">
        <span className="text-rose-400/70 text-[9px]">CANCEL</span>
        {event.runId != null && <span>run:{event.runId}</span>}
      </div>
    );
  }

  if (event.type === "memory_state") {
    return (
      <div className="font-mono text-[10px] text-muted-foreground/40 flex items-center gap-2 px-1 py-0.5">
        <span className="text-indigo-400/60 text-[9px]">MEMORY</span>
        <span>updated</span>
      </div>
    );
  }

  return null;
}
