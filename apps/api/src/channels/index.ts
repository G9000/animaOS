import { createAgentChannelAdapter } from "./adapters/agent";
import { createChannelRuntime } from "./runtime";
import type {
  ChannelAdapter,
  ChannelInboundMessage,
  ChannelName,
  ChannelReply,
} from "./types";

const runtime = createChannelRuntime({
  initialAdapters: [
    createAgentChannelAdapter("chat"),
    createAgentChannelAdapter("telegram"),
    createAgentChannelAdapter("webhook"),
    createAgentChannelAdapter("discord"),
  ],
});

export function registerChannelAdapter(adapter: ChannelAdapter): boolean {
  return runtime.register(adapter);
}

export function hasChannelAdapter(channel: ChannelName): boolean {
  return !!runtime.getAdapter(channel);
}

export function handleChannelMessage(
  message: ChannelInboundMessage,
): Promise<ChannelReply> {
  return runtime.handleMessage(message);
}

export function handleChannelStreamMessage(
  message: ChannelInboundMessage,
): AsyncGenerator<string> {
  return runtime.streamMessage(message);
}

export type {
  ChannelAdapter,
  ChannelInboundMessage,
  ChannelName,
  ChannelReply,
} from "./types";
