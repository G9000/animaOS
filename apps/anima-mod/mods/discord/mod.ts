/**
 * Discord Module
 * 
 * Connects ANIMA to Discord via Gateway WebSocket.
 * Based on apps/api/src/discord/gateway-relay.ts
 */

import { Elysia } from "elysia";
import type { Mod, ModContext } from "../../src/core/types.js";

interface DiscordConfig {
  token: string;
  intents?: number;
}

interface DiscordGatewayPayload {
  op: number;
  d: unknown;
  s: number | null;
  t: string | null;
}

interface DiscordGatewayHello {
  heartbeat_interval: number;
}

interface DiscordMessageCreateEvent {
  id: string;
  channel_id: string;
  content?: string;
  author?: { 
    id: string;
    username: string;
    bot?: boolean;
  };
}

const DISCORD_GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json";
const DEFAULT_INTENTS = 1 + 512 + 4096 + 32768; // guilds + guild/direct messages + message content

// Module state
let ws: WebSocket | null = null;
let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
let seq: number | null = null;
let reconnectAttempts = 0;
let modCtx: ModContext | null = null;
let botToken: string | null = null;
let intents: number = DEFAULT_INTENTS;

function clearTimers() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
  if (reconnectTimer) {
    clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
}

function send(payload: unknown) {
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify(payload));
}

function scheduleReconnect() {
  clearTimers();
  reconnectAttempts += 1;
  const delay = Math.min(30_000, 1_000 * Math.pow(2, reconnectAttempts - 1));

  reconnectTimer = setTimeout(() => {
    connect();
  }, delay);
}

function startHeartbeat(intervalMs: number) {
  if (heartbeatTimer) clearInterval(heartbeatTimer);

  heartbeatTimer = setInterval(() => {
    send({ op: 1, d: seq });
  }, intervalMs);
}

function identify() {
  send({
    op: 2,
    d: {
      token: botToken,
      intents,
      properties: {
        os: process.platform || "unknown",
        browser: "anima-mod",
        device: "anima-mod",
      },
    },
  });
}

async function handleMessage(event: DiscordMessageCreateEvent) {
  if (!modCtx || !botToken) return;
  if (!event.channel_id) return;
  if (!event.content?.trim()) return;
  if (event.author?.bot) return;

  const ctx = modCtx;
  const channelId = event.channel_id;
  const content = event.content;

  try {
    // Check if channel is linked to a user
    const userId = await ctx.anima.lookupUser("discord", channelId);
    if (userId === null) {
      // Not linked - send instructions
      await sendDiscordMessage(channelId, 
        "This channel is not linked to ANIMA. Use `/link <userId>` to connect.");
      return;
    }

    // Call cognitive core
    const result = await ctx.anima.chat({
      userId,
      message: content,
      context: { 
        source: "discord", 
        chatId: channelId,
        author: event.author?.username 
      },
    });

    // Send response
    await sendDiscordMessage(channelId, result.response);
  } catch (err) {
    ctx.logger.error("[discord] Handle message failed:", { error: err });
  }
}

async function sendDiscordMessage(channelId: string, content: string) {
  if (!botToken) return;

  // Discord has 2000 char limit for messages
  const chunks = splitMessage(content, 2000);
  
  for (const chunk of chunks) {
    const res = await fetch(`https://discord.com/api/v10/channels/${channelId}/messages`, {
      method: "POST",
      headers: {
        "Authorization": `Bot ${botToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ content: chunk }),
    });

    if (!res.ok) {
      console.error(`[discord] Failed to send message: ${res.status}`);
    }
  }
}

function splitMessage(text: string, maxLen: number): string[] {
  if (text.length <= maxLen) return [text];
  const chunks: string[] = [];
  let remaining = text;
  while (remaining.length > 0) {
    if (remaining.length <= maxLen) {
      chunks.push(remaining);
      break;
    }
    let splitAt = remaining.lastIndexOf("\n", maxLen);
    if (splitAt <= 0) splitAt = remaining.lastIndexOf(" ", maxLen);
    if (splitAt <= 0) splitAt = maxLen;
    chunks.push(remaining.slice(0, splitAt));
    remaining = remaining.slice(splitAt).trimStart();
  }
  return chunks;
}

function connect() {
  clearTimers();
  ws = new WebSocket(DISCORD_GATEWAY_URL);

  ws.onopen = () => {
    reconnectAttempts = 0;
    console.log("[discord] Connected to Discord Gateway");
  };

  ws.onmessage = (event) => {
    try {
      const payload = JSON.parse(
        typeof event.data === "string" ? event.data : String(event.data),
      ) as DiscordGatewayPayload;

      if (typeof payload.s === "number") {
        seq = payload.s;
      }

      if (payload.op === 10) {
        const hello = payload.d as DiscordGatewayHello;
        startHeartbeat(hello.heartbeat_interval);
        send({ op: 1, d: seq });
        identify();
        return;
      }

      if (payload.op === 7 || payload.op === 9) {
        console.warn("[discord] Reconnect requested by gateway");
        scheduleReconnect();
        return;
      }

      if (payload.op !== 0) return;
      if (payload.t !== "MESSAGE_CREATE") return;

      void handleMessage(payload.d as DiscordMessageCreateEvent).catch((err) => {
        console.error("[discord] Failed handling message:", err);
      });
    } catch (err) {
      console.error("[discord] Failed parsing payload:", err);
    }
  };

  ws.onerror = (err) => {
    console.error("[discord] WebSocket error:", err);
  };

  ws.onclose = () => {
    console.warn("[discord] Connection closed; scheduling reconnect");
    scheduleReconnect();
  };
}

export default {
  id: "discord",
  version: "1.0.0",

  configSchema: {
    token:   { type: "secret",  label: "Bot Token", required: true, description: "Token from Discord Developer Portal" },
    intents: { type: "number",  label: "Gateway Intents", default: 51351, description: "Bitfield for gateway intents" },
  },

  setupGuide: [
    { step: 1, title: "Create App",    instructions: "Go to discord.com/developers, create a new application, go to Bot tab, copy the token." },
    { step: 2, title: "Paste Token",   field: "token" },
    { step: 3, title: "Verify",        action: "healthcheck" },
  ],

  getRouter() {
    return new Elysia()
      .get("/", () => ({
        module: "discord",
        status: ws?.readyState === WebSocket.OPEN ? "connected" : "disconnected",
      }));
  },

  async init(ctx) {
    const token = ctx.config.token as string;
    if (!token) {
      throw new Error("Discord module requires 'token' in config");
    }

    botToken = token;
    intents = (ctx.config.intents as number) || DEFAULT_INTENTS;
    modCtx = ctx;

    ctx.logger.info("[discord] Module initialized");
  },

  async start() {
    if (!botToken) return;
    connect();
    modCtx?.logger.info("[discord] Gateway connection started");
  },

  async stop() {
    clearTimers();
    if (ws) {
      ws.close();
      ws = null;
    }
    modCtx?.logger.info("[discord] Stopped");
  },
} satisfies Mod;
