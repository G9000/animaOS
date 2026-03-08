export type ChannelName = "chat" | "telegram" | "webhook" | "discord";

export interface ChannelInboundMessage {
  channel: ChannelName;
  userId: number;
  text: string;
  receivedAt?: string;
  metadata?: Record<string, unknown>;
}

export interface ChannelReply {
  text: string;
  model?: string;
  provider?: string;
  toolsUsed?: string[];
}

export interface ChannelAdapter {
  readonly channel: ChannelName;
  handleMessage: (message: ChannelInboundMessage) => Promise<ChannelReply>;
  streamMessage?: (message: ChannelInboundMessage) => AsyncGenerator<string>;
}
