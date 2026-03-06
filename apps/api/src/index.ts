import { Hono } from "hono";
import { cors } from "hono/cors";
import auth from "./routes/auth";
import users from "./routes/users";
import chat from "./routes/chat";
import config from "./routes/config";
import memory from "./routes/memory";
import soul from "./routes/soul";


const app = new Hono();

// Middleware
app.use(
  "*",
  cors({
    origin: ["http://localhost:1420", "http://localhost:5173", "http://tauri.localhost"],
  }),
);

// Routes
app.route("/api/auth", auth);
app.route("/api/users", users);
app.route("/api/chat", chat);
app.route("/api/config", config);
app.route("/api/memory", memory);
app.route("/api/soul", soul);


// Health check
app.get("/", (c) => c.json({ message: "ANIMA API", version: "0.1.0" }));
app.get("/health", (c) => c.json({ status: "healthy" }));

export default {
  port: 3031,
  fetch: app.fetch,
};
