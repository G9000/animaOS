import { runAgent, streamAgent } from "../../agent";
import type {
  ChannelAdapter,
  ChannelInboundMessage,
  ChannelName,
} from "../types";

function toChannelReply(result: Awaited<ReturnType<typeof runAgent>>) {
  return {
    text: result.response,
    model: result.model,
    provider: result.provider,
    toolsUsed: result.toolsUsed,
  };
}

export function createAgentChannelAdapter(channel: ChannelName): ChannelAdapter {
  return {
    channel,
    async handleMessage(message: ChannelInboundMessage) {
      const result = await runAgent(message.text, message.userId);
      return toChannelReply(result);
    },
    async *streamMessage(message: ChannelInboundMessage) {
      for await (const chunk of streamAgent(message.text, message.userId)) {
        yield chunk;
      }
    },
  };
}
