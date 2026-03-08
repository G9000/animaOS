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
  channel_id: string;
  content?: string;
  author?: { bot?: boolean };
}

const DISCORD_GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json";
const DEFAULT_RELAY_URL = "http://127.0.0.1:3031/api/discord/webhook";
const DEFAULT_INTENTS = 1 + 512 + 4096 + 32768; // guilds + guild/direct messages + message content

let started = false;

function isEnabled(value: string | undefined): boolean {
  if (!value) return false;
  const normalized = value.trim().toLowerCase();
  return ["1", "true", "yes", "on"].includes(normalized);
}

function readConfig() {
  const token = process.env.DISCORD_BOT_TOKEN?.trim();
  const enabled = isEnabled(process.env.DISCORD_GATEWAY_RELAY);
  const relayUrl =
    process.env.DISCORD_GATEWAY_RELAY_URL?.trim() || DEFAULT_RELAY_URL;
  const webhookSecret = process.env.DISCORD_WEBHOOK_SECRET?.trim();
  const intents = Number(process.env.DISCORD_GATEWAY_INTENTS || DEFAULT_INTENTS);
  return { token, enabled, relayUrl, webhookSecret, intents };
}

async function forwardMessageCreate(
  relayUrl: string,
  webhookSecret: string | undefined,
  event: DiscordMessageCreateEvent,
) {
  if (!event.channel_id) return;
  if (!event.content?.trim()) return;
  if (event.author?.bot) return;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
  };
  if (webhookSecret) {
    headers["X-Discord-Webhook-Secret"] = webhookSecret;
  }

  const res = await fetch(relayUrl, {
    method: "POST",
    headers,
    body: JSON.stringify({
      channel_id: event.channel_id,
      content: event.content,
      author: event.author || { bot: false },
    }),
  });

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Relay POST failed: ${res.status} ${body}`);
  }
}

export function startDiscordGatewayRelay(): void {
  if (started) return;

  const { token, enabled, relayUrl, webhookSecret, intents } = readConfig();
  if (!enabled) return;

  if (!token) {
    console.warn(
      "[discord-gateway] DISCORD_GATEWAY_RELAY is enabled but DISCORD_BOT_TOKEN is missing.",
    );
    return;
  }

  started = true;

  let ws: WebSocket | null = null;
  let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let seq: number | null = null;
  let reconnectAttempts = 0;

  const clearTimers = () => {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  const send = (payload: unknown) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify(payload));
  };

  const scheduleReconnect = () => {
    clearTimers();
    reconnectAttempts += 1;
    const delay = Math.min(30_000, 1_000 * Math.pow(2, reconnectAttempts - 1));

    reconnectTimer = setTimeout(() => {
      connect();
    }, delay);
  };

  const startHeartbeat = (intervalMs: number) => {
    if (heartbeatTimer) clearInterval(heartbeatTimer);

    heartbeatTimer = setInterval(() => {
      send({ op: 1, d: seq });
    }, intervalMs);
  };

  const identify = () => {
    send({
      op: 2,
      d: {
        token,
        intents,
        properties: {
          os: process.platform || "unknown",
          browser: "anima-os-lite",
          device: "anima-os-lite",
        },
      },
    });
  };

  const connect = () => {
    clearTimers();
    ws = new WebSocket(DISCORD_GATEWAY_URL);

    ws.onopen = () => {
      reconnectAttempts = 0;
      console.log("[discord-gateway] Connected to Discord Gateway");
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
          // Heartbeat immediately after HELLO as recommended by Discord.
          send({ op: 1, d: seq });
          identify();
          return;
        }

        if (payload.op === 7 || payload.op === 9) {
          console.warn("[discord-gateway] Reconnect requested by gateway");
          scheduleReconnect();
          return;
        }

        if (payload.op !== 0) return;
        if (payload.t !== "MESSAGE_CREATE") return;

        void forwardMessageCreate(
          relayUrl,
          webhookSecret,
          payload.d as DiscordMessageCreateEvent,
        ).catch((err) => {
          console.error(
            "[discord-gateway] Failed forwarding message:",
            (err as Error).message,
          );
        });
      } catch (err) {
        console.error(
          "[discord-gateway] Failed parsing payload:",
          (err as Error).message,
        );
      }
    };

    ws.onerror = (err) => {
      console.error("[discord-gateway] WebSocket error:", err);
    };

    ws.onclose = () => {
      console.warn("[discord-gateway] Connection closed; scheduling reconnect");
      scheduleReconnect();
    };
  };

  connect();
}
