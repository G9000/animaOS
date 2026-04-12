import { describe, expect, test } from "bun:test";
import type { TraceEvent } from "@anima/api-client";

import {
  serializeTraceAsJson,
  serializeTraceAsText,
} from "../src/pages/chat/chat-trace";

describe("chat trace serializers", () => {
  const events: TraceEvent[] = [
    {
      type: "step_state",
      stepIndex: 0,
      phase: "request",
      messageCount: 2,
      allowedTools: ["send_message"],
      forceToolCall: true,
      messages: [
        { role: "system", chars: 13, preview: "system prompt" },
        { role: "user", chars: 5, preview: "hello" },
      ],
    },
    {
      type: "step_state",
      stepIndex: 0,
      phase: "result",
      assistantTextChars: 0,
      assistantTextPreview: "",
      toolCallCount: 0,
      reasoningChars: 0,
      reasoningCaptured: false,
    },
    {
      type: "warning",
      stepIndex: 0,
      code: "empty_step_result",
      message:
        "LLM returned no assistant text and no tool calls for this step.",
    },
    {
      type: "tool_call",
      stepIndex: 1,
      name: "send_message",
      arguments: { message: "hello world" },
      callId: "call-1",
    },
    {
      type: "timing",
      stepIndex: 1,
      ttftMs: 120,
      llmDurationMs: 850,
      stepDurationMs: 910,
    },
    {
      type: "done",
      status: "complete",
      stopReason: "terminal_tool",
      provider: "ollama",
      model: "qwen",
      toolsUsed: ["send_message"],
      retrieval: {
        retriever: "hybrid",
        citations: [
          {
            index: 1,
            memoryItemId: 7,
            uri: "memory://items/7",
            score: 0.91,
            category: "fact",
          },
        ],
        contextFragments: [
          {
            rank: 1,
            memoryItemId: 7,
            uri: "memory://items/7",
            text: "Important remembered detail.",
            score: 0.91,
            category: "fact",
          },
        ],
        stats: {
          retrievalMs: 12.5,
          totalConsidered: 4,
          returned: 1,
          cutoffIndex: 1,
          cutoffScore: 0.91,
          topScore: 0.91,
          cutoffRatio: 1,
          triggeredBy: "adaptive_ratio",
        },
      },
    },
  ];

  test("serializeTraceAsJson pretty-prints the full event array", () => {
    expect(serializeTraceAsJson(events)).toBe(JSON.stringify(events, null, 2));
  });

  test("serializeTraceAsText renders a readable timeline", () => {
    expect(serializeTraceAsText(events)).toBe(
      [
        "TRACE (6)",
        "[STEP 0 request] msgs=2 tools=1 force=true",
        "  allowed: send_message",
        "  - system (13 chars): system prompt",
        "  - user (5 chars): hello",
        "[STEP 0 result] text=0 toolCalls=0 reasoning=0 captured=false",
        "[WARN 0 empty_step_result] LLM returned no assistant text and no tool calls for this step.",
        '[CALL 1] send_message {"message":"hello world"}',
        "[TIME 1] ttft=120ms llm=850ms step=910ms",
        "[DONE] status=complete stop=terminal_tool provider=ollama model=qwen tools=send_message retrieval=hybrid 1/4 hits 12.5ms",
      ].join("\n"),
    );
  });
});
