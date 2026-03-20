import { Hono } from "hono";
import { cors } from "hono/cors";
import { startDiscordGatewayRelay } from "./discord/gateway-relay";

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
app.get("/health", (c) => c.json({ status: "healthy", service: "bot-gateway" }));

// Discord gateway relay (connects via WebSocket, forwards to Python API)
startDiscordGatewayRelay();

export default {
  port: 3033,
  hostname: "127.0.0.1",
  fetch: app.fetch,
};

export { app };
