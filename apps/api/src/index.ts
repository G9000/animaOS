import { Hono } from "hono";
import { cors } from "hono/cors";
import auth from "./routes/auth";
import users from "./routes/users";
import chat from "./routes/chat";
import config from "./routes/config";
import memory from "./routes/memory";
import soul from "./routes/soul";
import telegram from "./routes/telegram";
import discord from "./routes/discord";
import tasks from "./routes/tasks";
import channel from "./routes/channel";
import { startTaskReminderCron } from "./cron/task-reminders";
import { ensureRuntimeLayout } from "./lib/runtime-paths";
import { startDiscordGatewayRelay } from "./discord/gateway-relay";

await ensureRuntimeLayout();

const app = new Hono();

// Middleware
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
  }),
);

// Routes
app.route("/api/auth", auth);
app.route("/api/users", users);
app.route("/api/chat", chat);
app.route("/api/config", config);
app.route("/api/memory", memory);
app.route("/api/soul", soul);
app.route("/api/telegram", telegram);
app.route("/api/discord", discord);
app.route("/api/tasks", tasks);
app.route("/api/channel", channel);


// Health check
app.get("/", (c) => c.json({ message: "ANIMA API", version: "0.1.0" }));
app.get("/health", (c) => c.json({ status: "healthy" }));

// Start background crons
startTaskReminderCron();
startDiscordGatewayRelay();

export default {
  port: 3031,
  hostname: "127.0.0.1",
  fetch: app.fetch,
};
