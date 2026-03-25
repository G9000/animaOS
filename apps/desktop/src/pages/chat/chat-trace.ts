import type { TraceEvent, TraceMessagePreview } from "@anima/api-client";

export function serializeTraceAsJson(events: TraceEvent[]): string {
  return JSON.stringify(events, null, 2);
}

export function serializeTraceAsText(events: TraceEvent[]): string {
  const lines = [`TRACE (${events.length})`];

  for (const event of events) {
    lines.push(...formatTraceEvent(event));
  }

  return lines.join("\n");
}

function formatTraceEvent(event: TraceEvent): string[] {
  switch (event.type) {
    case "step_state":
      return event.phase === "request"
        ? formatStepRequest(event)
        : formatStepResult(event);
    case "warning":
      return [
        `[WARN ${event.stepIndex ?? 0} ${event.code ?? "warning"}] ${event.message ?? ""}`.trim(),
      ];
    case "tool_call":
      return [
        `[CALL ${event.stepIndex ?? 0}] ${event.name ?? "unknown"} ${compactJson(event.arguments)}`.trim(),
      ];
    case "tool_return":
      return [
        `[RET ${event.stepIndex ?? 0}] ${event.name ?? "unknown"} error=${String(Boolean(event.isError))} ${stringValue(event.output)}`.trim(),
      ];
    case "usage":
      return [
        `[TOKENS] in=${event.promptTokens ?? 0} out=${event.completionTokens ?? 0} total=${event.totalTokens ?? 0} reason=${event.reasoningTokens ?? 0} cached=${event.cachedInputTokens ?? 0}`,
      ];
    case "timing":
      return [
        `[TIME ${event.stepIndex ?? 0}] ttft=${event.ttftMs ?? 0}ms llm=${event.llmDurationMs ?? 0}ms step=${event.stepDurationMs ?? 0}ms`,
      ];
    case "done":
      return [
        `[DONE] status=${event.status ?? ""} stop=${event.stopReason ?? ""} provider=${event.provider ?? ""} model=${event.model ?? ""} tools=${(event.toolsUsed ?? []).join(",")}`.trim(),
      ];
    case "approval_pending":
      return [
        `[WAIT] run=${event.runId ?? 0} tool=${event.name ?? "unknown"} ${compactJson(event.arguments)}`.trim(),
      ];
    case "cancelled":
      return [`[CANCEL] run=${event.runId ?? 0}`];
    default:
      return [compactJson(event)];
  }
}

function formatStepRequest(event: TraceEvent): string[] {
  const lines = [
    `[STEP ${event.stepIndex ?? 0} request] msgs=${event.messageCount ?? 0} tools=${event.allowedTools?.length ?? 0} force=${String(Boolean(event.forceToolCall))}`,
  ];

  if (event.allowedTools && event.allowedTools.length > 0) {
    lines.push(`  allowed: ${event.allowedTools.join(", ")}`);
  }

  for (const message of event.messages ?? []) {
    lines.push(`  - ${formatTraceMessage(message)}`);
  }

  return lines;
}

function formatStepResult(event: TraceEvent): string[] {
  return [
    `[STEP ${event.stepIndex ?? 0} result] text=${event.assistantTextChars ?? 0} toolCalls=${event.toolCallCount ?? 0} reasoning=${event.reasoningChars ?? 0} captured=${String(Boolean(event.reasoningCaptured))}`,
  ];
}

function formatTraceMessage(message: TraceMessagePreview): string {
  return `${message.role} (${message.chars} chars): ${normalizePreview(message.preview)}`;
}

function compactJson(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return stringValue(value);
  }
}

function stringValue(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (value == null) {
    return "";
  }
  return String(value);
}

function normalizePreview(value: string): string {
  return value.replace(/\n/g, "\\n");
}
