/**
 * Google Integration Mod
 *
 * Provides Gmail and Calendar capabilities for ANIMA.
 * Self-contained — OAuth, token storage, and API calls live here.
 * The cognitive core calls this mod via HTTP to execute tools.
 */

import { Elysia, t } from "elysia";
import type { Mod, ModContext } from "../../src/core/types.js";
import {
  buildAuthUrl,
  exchangeCode,
  generateState,
  getUserEmail,
  getValidAccessToken,
  revokeToken,
} from "./oauth.js";
import { searchGmail, readGmail, sendGmail } from "./gmail.js";
import { listCalendarEvents, createCalendarEvent } from "./calendar.js";

interface GoogleConfig {
  clientId: string;
  clientSecret: string;
  redirectUri?: string;
}

let modCtx: ModContext | null = null;
let config: GoogleConfig | null = null;

function getRedirectUri(): string {
  return config?.redirectUri ?? "http://127.0.0.1:3034/google/callback";
}

async function requireToken(userId: number): Promise<string> {
  if (!modCtx || !config) {
    throw new Error("Google mod is not initialized");
  }
  const token = await getValidAccessToken(
    modCtx,
    config.clientId,
    config.clientSecret,
    userId,
  );
  if (!token) {
    throw new Error(
      "Google account not connected. Please connect your Google account in the Google mod settings.",
    );
  }
  return token;
}

export default {
  id: "google",
  version: "1.0.0",

  configSchema: {
    clientId: {
      type: "secret",
      label: "Google Client ID",
      required: true,
      description: "OAuth 2.0 Client ID from Google Cloud Console",
    },
    clientSecret: {
      type: "secret",
      label: "Google Client Secret",
      required: true,
      description: "OAuth 2.0 Client Secret from Google Cloud Console",
    },
    redirectUri: {
      type: "string",
      label: "Redirect URI",
      default: "http://127.0.0.1:3034/google/callback",
      description: "Must match the authorized redirect URI in Google Cloud Console",
    },
  },

  setupGuide: [
    {
      step: 1,
      title: "Create OAuth Credentials",
      instructions:
        "Go to Google Cloud Console → APIs & Services → Credentials → Create OAuth client ID. Choose 'Desktop app' or 'Web application'. Add http://127.0.0.1:3034/google/callback as an authorized redirect URI.",
    },
    {
      step: 2,
      title: "Enable APIs",
      instructions:
        "In Google Cloud Console, enable the Gmail API and Google Calendar API for your project.",
    },
    {
      step: 3,
      title: "Enter Credentials",
      field: "clientId",
    },
    {
      step: 4,
      title: "Enter Secret",
      field: "clientSecret",
    },
    {
      step: 5,
      title: "Verify",
      action: "healthcheck",
    },
  ],

  toolSchemas: [
    {
      name: "search_gmail",
      description:
        "Search the user's Gmail inbox. Supports Gmail search syntax (from:, to:, subject:, after:YYYY/MM/DD, etc.).",
      endpoint: "/gmail/search",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "Gmail search query" },
          maxResults: { type: "integer", description: "Max results to return (1–20)", default: 10 },
        },
        required: ["query"],
      },
    },
    {
      name: "read_gmail",
      description: "Read the full content of a specific Gmail message by its ID.",
      endpoint: "/gmail/read",
      parameters: {
        type: "object",
        properties: {
          messageId: { type: "string", description: "Gmail message ID" },
        },
        required: ["messageId"],
      },
    },
    {
      name: "send_gmail",
      description: "Send an email via Gmail.",
      endpoint: "/gmail/send",
      parameters: {
        type: "object",
        properties: {
          to: { type: "string", description: "Recipient email address" },
          subject: { type: "string", description: "Email subject" },
          body: { type: "string", description: "Email body (plain text)" },
        },
        required: ["to", "subject", "body"],
      },
    },
    {
      name: "list_calendar_events",
      description: "List Google Calendar events in a date range. Dates should be YYYY-MM-DD or ISO 8601.",
      endpoint: "/calendar/events",
      parameters: {
        type: "object",
        properties: {
          startDate: { type: "string", description: "Range start (YYYY-MM-DD or ISO 8601)" },
          endDate: { type: "string", description: "Range end (YYYY-MM-DD or ISO 8601)" },
          maxResults: { type: "integer", description: "Max events to return (1–50)", default: 10 },
        },
        required: ["startDate", "endDate"],
      },
    },
    {
      name: "create_calendar_event",
      description:
        "Create a Google Calendar event. start_time and end_time should be ISO 8601 (e.g. 2026-04-26T14:00:00). attendees is a comma-separated list of emails.",
      endpoint: "/calendar/events/create",
      parameters: {
        type: "object",
        properties: {
          summary: { type: "string", description: "Event title" },
          startTime: { type: "string", description: "Start time (ISO 8601)" },
          endTime: { type: "string", description: "End time (ISO 8601)" },
          description: { type: "string", description: "Event description (optional)" },
          attendees: { type: "array", items: { type: "string" }, description: "List of attendee email addresses (optional)" },
        },
        required: ["summary", "startTime", "endTime"],
      },
    },
  ],

  async init(ctx) {
    const clientId = ctx.config.clientId as string | undefined;
    const clientSecret = ctx.config.clientSecret as string | undefined;

    if (!clientId || !clientSecret) {
      throw new Error("Google mod requires 'clientId' and 'clientSecret' in config");
    }

    config = {
      clientId,
      clientSecret,
      redirectUri: (ctx.config.redirectUri as string) || undefined,
    };
    modCtx = ctx;

    ctx.logger.info("[google] Module initialized");
  },

  getRouter() {
    return new Elysia()
      .onError(({ error, set }) => {
        set.status = 200;
        const msg = error instanceof Error ? error.message : String(error);
        modCtx?.logger.warn("[google] Route error", { error: msg });
        return { error: msg };
      })
      // Health / info
      .get("/", () => ({
        module: "google",
        status: modCtx ? "initialized" : "not_initialized",
      }))

      // Generate OAuth URL for a user
      .get("/auth-url", async ({ query }) => {
        if (!modCtx || !config) {
          return { error: "Mod not initialized" };
        }
        const userId = Number(query.userId);
        if (!userId || Number.isNaN(userId)) {
          return { error: "Missing or invalid userId" };
        }

        const state = generateState();
        await modCtx.store.set(`google:auth_state:${state}`, {
          userId,
          createdAt: Date.now(),
        });

        const url = buildAuthUrl(config.clientId, getRedirectUri(), state);
        return { authUrl: url };
      })

      // OAuth callback from Google
      .get("/callback", async ({ query }) => {
        if (!modCtx || !config) {
          return { error: "Mod not initialized" };
        }

        const code = String(query.code ?? "");
        const state = String(query.state ?? "");
        const error = String(query.error ?? "");

        if (error) {
          return { error: `OAuth error: ${error}` };
        }
        if (!code || !state) {
          return { error: "Missing code or state" };
        }

        // Look up state
        const authState = await modCtx.store.get<{ userId: number; createdAt: number }>(
          `google:auth_state:${state}`,
        );
        await modCtx.store.delete(`google:auth_state:${state}`);

        if (!authState) {
          return { error: "Invalid or expired state" };
        }
        if (Date.now() - authState.createdAt > 10 * 60 * 1000) {
          return { error: "State expired" };
        }

        try {
          const tokens = await exchangeCode(config.clientId, config.clientSecret, getRedirectUri(), code);
          const email = await getUserEmail(tokens.accessToken);

          await modCtx.store.set(`google:tokens:${authState.userId}`, {
            email,
            accessToken: tokens.accessToken,
            refreshToken: tokens.refreshToken,
            expiresAt: tokens.expiresAt,
          });

          modCtx?.logger.info("[google] Account connected", { userId: authState.userId, email });
          return {
            success: true,
            email,
            message: `Google account ${email} connected successfully. You can close this window.`,
          };
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          modCtx?.logger.error("[google] OAuth callback failed", { error: msg });
          return { error: msg };
        }
      })

      // Check connection status
      .get("/status", async ({ query }) => {
        if (!modCtx) return { connected: false };
        const userId = Number(query.userId);
        if (!userId || Number.isNaN(userId)) {
          return { error: "Missing or invalid userId" };
        }

        const tokens = await modCtx.store.get<{ email: string }>(`google:tokens:${userId}`);
        return {
          connected: !!tokens,
          email: tokens?.email ?? null,
        };
      })

      // Disconnect
      .post("/disconnect", async ({ body }) => {
        if (!modCtx) return { error: "Mod not initialized" };
        const userId = Number((body as Record<string, unknown>).userId);
        if (!userId || Number.isNaN(userId)) {
          return { error: "Missing or invalid userId" };
        }

        const tokens = await modCtx.store.get<{ refreshToken: string }>(
          `google:tokens:${userId}`,
        );
        if (tokens?.refreshToken) {
          try {
            await revokeToken(tokens.refreshToken);
          } catch { /* ignore */ }
        }
        await modCtx.store.delete(`google:tokens:${userId}`);
        return { success: true };
      })

      // Gmail: search
      .post(
        "/gmail/search",
        async ({ body }) => {
          const b = body as Record<string, unknown>;
          const userId = Number(b.userId);
          const query = String(b.query ?? "");
          const maxResults = Number(b.maxResults ?? 10);

          const token = await requireToken(userId);
          const result = await searchGmail(token, query, maxResults);
          return { result };
        },
        {
          body: t.Object({
            userId: t.Number(),
            query: t.String(),
            maxResults: t.Number({ default: 10 }),
          }),
        },
      )

      // Gmail: read
      .post(
        "/gmail/read",
        async ({ body }) => {
          const b = body as Record<string, unknown>;
          const userId = Number(b.userId);
          const messageId = String(b.messageId ?? "");

          const token = await requireToken(userId);
          const result = await readGmail(token, messageId);
          return { result };
        },
        {
          body: t.Object({
            userId: t.Number(),
            messageId: t.String(),
          }),
        },
      )

      // Gmail: send
      .post(
        "/gmail/send",
        async ({ body }) => {
          const b = body as Record<string, unknown>;
          const userId = Number(b.userId);
          const to = String(b.to ?? "");
          const subject = String(b.subject ?? "");
          const bodyText = String(b.body ?? "");

          const token = await requireToken(userId);
          const result = await sendGmail(token, to, subject, bodyText);
          return { result };
        },
        {
          body: t.Object({
            userId: t.Number(),
            to: t.String(),
            subject: t.String(),
            body: t.String(),
          }),
        },
      )

      // Calendar: list events
      .post(
        "/calendar/events",
        async ({ body }) => {
          const b = body as Record<string, unknown>;
          const userId = Number(b.userId);
          const startDate = String(b.startDate ?? "");
          const endDate = String(b.endDate ?? "");
          const maxResults = Number(b.maxResults ?? 10);

          const token = await requireToken(userId);
          const result = await listCalendarEvents(token, startDate, endDate, maxResults);
          return { result };
        },
        {
          body: t.Object({
            userId: t.Number(),
            startDate: t.String(),
            endDate: t.String(),
            maxResults: t.Number({ default: 10 }),
          }),
        },
      )

      // Calendar: create event
      .post(
        "/calendar/events/create",
        async ({ body }) => {
          const b = body as Record<string, unknown>;
          const userId = Number(b.userId);
          const summary = String(b.summary ?? "");
          const startTime = String(b.startTime ?? "");
          const endTime = String(b.endTime ?? "");
          const description = b.description ? String(b.description) : undefined;
          const attendees = Array.isArray(b.attendees)
            ? b.attendees.map(String)
            : typeof b.attendees === "string" && b.attendees
              ? b.attendees.split(",").map((s) => s.trim()).filter(Boolean)
              : undefined;

          const token = await requireToken(userId);
          const result = await createCalendarEvent(
            token,
            summary,
            startTime,
            endTime,
            description,
            attendees,
          );
          return { result };
        },
        {
          body: t.Object({
            userId: t.Number(),
            summary: t.String(),
            startTime: t.String(),
            endTime: t.String(),
            description: t.Optional(t.String()),
            attendees: t.Optional(t.Array(t.String())),
          }),
        },
      );
  },

  async start() {
    modCtx?.logger.info("[google] Module started");
  },

  async stop() {
    modCtx?.logger.info("[google] Module stopped");
    modCtx = null;
    config = null;
  },
} satisfies Mod;
