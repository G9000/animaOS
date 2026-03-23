/**
 * Telegram Module
 * 
 * Connects ANIMA to Telegram via Grammy.
 * Connects ANIMA to Telegram via Grammy.
 */

import { Elysia } from "elysia";
import { Bot, webhookCallback } from "grammy";
import type { Mod, ModContext } from "../../src/core/types.js";

// Module state
let bot: Bot | null = null;
let modCtx: ModContext | null = null;
let linkSecret: string | undefined;
const MAX_TG_LENGTH = 4096;

function splitMessage(text: string): string[] {
  if (text.length <= MAX_TG_LENGTH) return [text];
  const chunks: string[] = [];
  let remaining = text;
  while (remaining.length > 0) {
    if (remaining.length <= MAX_TG_LENGTH) {
      chunks.push(remaining);
      break;
    }
    let splitAt = remaining.lastIndexOf("\n", MAX_TG_LENGTH);
    if (splitAt <= 0) splitAt = remaining.lastIndexOf(" ", MAX_TG_LENGTH);
    if (splitAt <= 0) splitAt = MAX_TG_LENGTH;
    chunks.push(remaining.slice(0, splitAt));
    remaining = remaining.slice(splitAt).trimStart();
  }
  return chunks;
}

// /start
function setupHandlers(bot: Bot, ctx: ModContext) {
  bot.command("start", async (tgCtx) => {
    const parts = [
      "ANIMA is online.",
      "",
      "Use /link <userId>" + (linkSecret ? " <linkSecret>" : "") + " to connect this chat.",
      "Use /unlink to disconnect.",
    ];
    await tgCtx.reply(parts.join("\n"));
  });

  // /link <userId> [linkSecret]
  bot.command("link", async (tgCtx) => {
    const args = tgCtx.match.split(/\s+/).filter(Boolean);
    const userId = Number(args[0]);

    if (!args[0] || !Number.isInteger(userId) || userId <= 0) {
      await tgCtx.reply(
        "Usage: /link <userId>" + (linkSecret ? " <linkSecret>" : ""),
      );
      return;
    }

    const secret = args[1];

    try {
      await ctx.anima.linkChannel("telegram", String(tgCtx.chat.id), userId, secret);
      await tgCtx.reply(`Linked to user ${userId}. You can now chat with ANIMA here.`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      if (msg.includes("403")) {
        await tgCtx.reply("Invalid link secret.");
      } else if (msg.includes("404")) {
        await tgCtx.reply(`User ${userId} not found.`);
      } else {
        ctx.logger.error("[telegram] link failed:", { error: msg });
        await tgCtx.reply("Failed to link. Check logs for details.");
      }
    }
  });

  // /unlink
  bot.command("unlink", async (tgCtx) => {
    try {
      await ctx.anima.unlinkChannel("telegram", String(tgCtx.chat.id));
      await tgCtx.reply("Chat unlinked.");
    } catch (err) {
      ctx.logger.error("[telegram] unlink failed:", { error: err });
      await tgCtx.reply("Failed to unlink.");
    }
  });

  // Regular messages
  bot.on("message:text", async (tgCtx) => {
    const chatId = tgCtx.chat.id;
    const text = tgCtx.message.text;

    const userId = await ctx.anima.lookupUser("telegram", String(chatId));
    if (userId === null) {
      await tgCtx.reply(
        "This chat is not linked. Use /link <userId>" +
          (linkSecret ? " <linkSecret>" : "") +
          " to connect.",
      );
      return;
    }

    await tgCtx.replyWithChatAction("typing");

    try {
      const result = await ctx.anima.chat({
        userId,
        message: text,
        context: { source: "telegram", chatId: String(chatId) },
      });

      const chunks = splitMessage(result.response || "[empty response]");
      for (const chunk of chunks) {
        await tgCtx.reply(chunk);
      }
    } catch (err) {
      ctx.logger.error("[telegram] chat failed:", { error: err });
      await tgCtx.reply("Something went wrong. Please try again.");
    }
  });
}

export default {
  id: "telegram",
  version: "1.0.0",

  configSchema: {
    token:         { type: "secret",  label: "Bot Token", required: true, description: "Token from @BotFather" },
    mode:          { type: "enum",    label: "Connection Mode", options: ["polling", "webhook"], default: "polling" },
    webhookUrl:    { type: "string",  label: "Webhook URL", showWhen: { mode: "webhook" } },
    webhookSecret: { type: "secret",  label: "Webhook Secret", showWhen: { mode: "webhook" } },
    linkSecret:    { type: "secret",  label: "Link Secret", description: "Optional secret for /link command" },
  },

  setupGuide: [
    { step: 1, title: "Create Bot",       instructions: "Open Telegram, search @BotFather, send /newbot, follow the prompts to create your bot." },
    { step: 2, title: "Paste Token",      field: "token" },
    { step: 3, title: "Connection Mode",  field: "mode" },
    { step: 4, title: "Verify",           action: "healthcheck" },
  ],

  getRouter() {
    const router = new Elysia();
    
    // Only set up webhook endpoint if in webhook mode
    if (modCtx?.config.mode === "webhook" && bot) {
      const webhookSecret = modCtx.config.webhookSecret as string | undefined;
      const handleUpdate = webhookCallback(bot, "std/http", {
        secretToken: webhookSecret,
      });

      router.post("/webhook", async ({ request }) => {
        return handleUpdate(request);
      });
    }

    // Health check
    router.get("/", () => ({
      module: "telegram",
      status: bot ? "running" : "not initialized",
      mode: (modCtx?.config.mode as string) || "polling",
    }));

    return router;
  },

  async init(ctx) {
    const token = ctx.config.token as string;
    if (!token) {
      throw new Error("Telegram module requires 'token' in config");
    }

    linkSecret = ctx.config.linkSecret as string | undefined;
    bot = new Bot(token);
    modCtx = ctx;

    setupHandlers(bot, ctx);
    ctx.logger.info("[telegram] Bot initialized");
  },

  async start() {
    if (!bot || !modCtx) return;

    const mode = (modCtx.config.mode as string) || "polling";

    if (mode === "webhook") {
      const webhookUrl = modCtx.config.webhookUrl as string | undefined;
      const webhookSecret = modCtx.config.webhookSecret as string | undefined;

      if (webhookUrl) {
        await bot.api.setWebhook(webhookUrl, {
          secret_token: webhookSecret,
        });
        modCtx.logger.info("[telegram] Webhook registered", { url: webhookUrl });
      }
    } else {
      await bot.start();
      modCtx.logger.info("[telegram] Polling started");
    }
  },

  async stop() {
    if (bot) {
      await bot.stop();
      modCtx?.logger.info("[telegram] Stopped");
    }
  },
} satisfies Mod;
