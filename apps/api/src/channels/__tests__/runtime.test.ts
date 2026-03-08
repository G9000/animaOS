import { describe, expect, test } from "bun:test";
import { createChannelRuntime } from "../runtime";

describe("channel runtime", () => {
  test("register rejects duplicate channel adapter", () => {
    const runtime = createChannelRuntime();
    const adapter = {
      channel: "chat" as const,
      handleMessage: async () => ({ text: "ok" }),
    };

    expect(runtime.register(adapter)).toBe(true);
    expect(runtime.register(adapter)).toBe(false);
  });

  test("handleMessage routes to the matching adapter", async () => {
    const runtime = createChannelRuntime({
      initialAdapters: [
        {
          channel: "chat",
          handleMessage: async (message) => ({
            text: `echo:${message.text}`,
          }),
        },
      ],
    });

    const result = await runtime.handleMessage({
      channel: "chat",
      userId: 1,
      text: "hello",
    });

    expect(result.text).toBe("echo:hello");
  });

  test("handleMessage throws when channel has no adapter", async () => {
    const runtime = createChannelRuntime();

    await expect(
      runtime.handleMessage({
        channel: "telegram",
        userId: 1,
        text: "hello",
      }),
    ).rejects.toThrow('No channel adapter registered for "telegram".');
  });

  test("streamMessage routes chunks to the matching adapter", async () => {
    const runtime = createChannelRuntime({
      initialAdapters: [
        {
          channel: "chat",
          handleMessage: async () => ({ text: "unused" }),
          async *streamMessage(message) {
            yield `${message.text}-1`;
            yield `${message.text}-2`;
          },
        },
      ],
    });

    const chunks: string[] = [];
    for await (const chunk of runtime.streamMessage({
      channel: "chat",
      userId: 1,
      text: "hello",
    })) {
      chunks.push(chunk);
    }

    expect(chunks).toEqual(["hello-1", "hello-2"]);
  });

  test("streamMessage throws when adapter does not support streaming", () => {
    const runtime = createChannelRuntime({
      initialAdapters: [
        {
          channel: "chat",
          handleMessage: async () => ({ text: "ok" }),
        },
      ],
    });

    expect(() =>
      runtime.streamMessage({
        channel: "chat",
        userId: 1,
        text: "hello",
      }),
    ).toThrow('Channel adapter "chat" does not support streaming.');
  });
});
