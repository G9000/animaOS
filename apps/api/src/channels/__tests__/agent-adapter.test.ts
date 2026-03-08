import { describe, expect, mock, test } from "bun:test";

const runAgentMock = mock(async () => ({
  response: "agent reply",
  model: "qwen3:14b",
  provider: "ollama",
  toolsUsed: ["remember"],
}));
const streamAgentMock = mock(async function* () {
  yield "chunk-1";
  yield "chunk-2";
});

mock.module("../../agent", () => ({
  runAgent: runAgentMock,
  streamAgent: streamAgentMock,
}));

const { createAgentChannelAdapter } = await import("../adapters/agent");

describe("agent channel adapter", () => {
  test("maps runAgent result into channel reply shape", async () => {
    const adapter = createAgentChannelAdapter("chat");
    const result = await adapter.handleMessage({
      channel: "chat",
      userId: 7,
      text: "hello",
    });

    expect(runAgentMock).toHaveBeenCalledTimes(1);
    expect(runAgentMock).toHaveBeenCalledWith("hello", 7);
    expect(result).toEqual({
      text: "agent reply",
      model: "qwen3:14b",
      provider: "ollama",
      toolsUsed: ["remember"],
    });
  });

  test("maps streamAgent into chunk stream", async () => {
    const adapter = createAgentChannelAdapter("chat");
    const chunks: string[] = [];

    for await (const chunk of adapter.streamMessage!({
      channel: "chat",
      userId: 7,
      text: "stream this",
    })) {
      chunks.push(chunk);
    }

    expect(streamAgentMock).toHaveBeenCalledTimes(1);
    expect(streamAgentMock).toHaveBeenCalledWith("stream this", 7);
    expect(chunks).toEqual(["chunk-1", "chunk-2"]);
  });
});
