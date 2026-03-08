import type {
  ChannelAdapter,
  ChannelInboundMessage,
  ChannelName,
  ChannelReply,
} from "./types";

export interface ChannelRuntime {
  register: (adapter: ChannelAdapter) => boolean;
  getAdapter: (channel: ChannelName) => ChannelAdapter | undefined;
  handleMessage: (message: ChannelInboundMessage) => Promise<ChannelReply>;
  streamMessage: (message: ChannelInboundMessage) => AsyncGenerator<string>;
}

export interface ChannelRuntimeDeps {
  initialAdapters?: readonly ChannelAdapter[];
}

export function createChannelRuntime(
  deps: ChannelRuntimeDeps = {},
): ChannelRuntime {
  const adapters = new Map<ChannelName, ChannelAdapter>();

  for (const adapter of deps.initialAdapters ?? []) {
    adapters.set(adapter.channel, adapter);
  }

  function register(adapter: ChannelAdapter): boolean {
    if (adapters.has(adapter.channel)) return false;
    adapters.set(adapter.channel, adapter);
    return true;
  }

  function getAdapter(channel: ChannelName): ChannelAdapter | undefined {
    return adapters.get(channel);
  }

  async function handleMessage(
    message: ChannelInboundMessage,
  ): Promise<ChannelReply> {
    const adapter = adapters.get(message.channel);
    if (!adapter) {
      throw new Error(`No channel adapter registered for "${message.channel}".`);
    }
    return adapter.handleMessage(message);
  }

  function streamMessage(
    message: ChannelInboundMessage,
  ): AsyncGenerator<string> {
    const adapter = adapters.get(message.channel);
    if (!adapter) {
      throw new Error(`No channel adapter registered for "${message.channel}".`);
    }
    if (!adapter.streamMessage) {
      throw new Error(
        `Channel adapter "${message.channel}" does not support streaming.`,
      );
    }
    return adapter.streamMessage(message);
  }

  return {
    register,
    getAdapter,
    handleMessage,
    streamMessage,
  };
}
