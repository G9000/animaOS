import type { ChatMessage, TraceEvent } from "./types";

export function shouldGroupMessages(
  current: ChatMessage,
  previous: ChatMessage | null,
): boolean {
  if (!previous) return false;
  if (current.role !== previous.role) return false;

  // Group if within 5 minutes
  const currentTime = current.createdAt
    ? new Date(current.createdAt).getTime()
    : 0;
  const previousTime = previous.createdAt
    ? new Date(previous.createdAt).getTime()
    : 0;
  const diffMs = Math.abs(currentTime - previousTime);
  return diffMs < 5 * 60 * 1000;
}

export function formatTimestamp(dateStr: string | undefined): string | null {
  if (!dateStr) return null;
  const dt = new Date(dateStr);
  if (Number.isNaN(dt.getTime())) return null;
  return dt.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatFullTimestamp(dateStr: string | undefined): string | null {
  if (!dateStr) return null;
  const dt = new Date(dateStr);
  if (Number.isNaN(dt.getTime())) return null;
  return dt.toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function formatJson(value: unknown): string {
  try {
    if (typeof value === "string") {
      return JSON.stringify(JSON.parse(value), null, 2);
    }
    return JSON.stringify(value, null, 2);
  } catch {
    return typeof value === "string" ? value : String(value);
  }
}

export function serializeTraceAsJson(events: TraceEvent[]): string {
  return JSON.stringify(events, null, 2);
}

export function serializeTraceAsText(events: TraceEvent[]): string {
  return events
    .map((evt) => {
      if (evt.type === "step_state") {
        return `[STEP ${evt.stepIndex ?? 0}] ${evt.phase}: msgs=${evt.messageCount ?? 0} tools=${evt.allowedTools?.length ?? 0}`;
      }
      if (evt.type === "warning") {
        return `[WARN] ${evt.code}: ${evt.message ?? ""}`;
      }
      if (evt.type === "tool_call") {
        return `[CALL] ${evt.name}`;
      }
      if (evt.type === "tool_return") {
        return `[${evt.isError ? "ERR" : "RET"}] ${evt.name}`;
      }
      if (evt.type === "usage") {
        return `[TOKENS] ${evt.promptTokens ?? 0}in + ${evt.completionTokens ?? 0}out = ${evt.totalTokens ?? 0}`;
      }
      if (evt.type === "timing") {
        return `[TIME] step:${evt.stepDurationMs ?? 0}ms`;
      }
      if (evt.type === "done") {
        return `[DONE] ${evt.provider ?? ""} ${evt.model ?? ""}`;
      }
      return `[${evt.type.toUpperCase()}]`;
    })
    .join("\n");
}
