import { Hono } from "hono";
import { cors } from "hono/cors";
import { webhookCallback } from "grammy";
import { startDiscordGatewayRelay } from "./discord/gateway-relay";
import { animaApi } from "./lib/anima-api";

const app = new Hono();

app.use(
  "*",
  cors({
    origin: [
      "http://localhost:1420",
      "http://localhost:5173",
      "http://tauri.localhost",
      "https://tauri.localhost",
      "tauri://localhost",
    ],
    credentials: true,
  }),
);

// Health
app.get("/", (c) => c.json({ name: "ANIMA Bot Gateway", version: "0.2.0" }));
app.get("/health", (c) =>
  c.json({ status: "healthy", service: "bot-gateway" }),
);

// Telegram webhook (conditional)
if (process.env.TELEGRAM_BOT_TOKEN) {
  const { bot } = await import("./telegram/bot");
  const { setupTelegramWebhook } = await import("./telegram/setup");

  const handleUpdate = webhookCallback(bot, "std/http", {
    secretToken: process.env.TELEGRAM_WEBHOOK_SECRET,
  });
  app.post("/api/telegram/webhook", (c) => handleUpdate(c.req.raw));

  // Authenticate with Python API, then register webhook
  animaApi
    .login()
    .then(() => setupTelegramWebhook())
    .catch((err) =>
      console.error("[startup] Telegram setup failed:", err.message),
    );
}

// Discord gateway relay
startDiscordGatewayRelay();

export default {
  port: 3033,
  hostname: "127.0.0.1",
  fetch: app.fetch,
};

export { app };
