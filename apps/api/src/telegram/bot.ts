import { Bot } from "grammy";
import { animaApi } from "../lib/anima-api";

const token = process.env.TELEGRAM_BOT_TOKEN || "";

export const bot = new Bot(token);

const LINK_SECRET = process.env.TELEGRAM_LINK_SECRET;
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
bot.command("start", async (ctx) => {
  const parts = [
    "ANIMA is online.",
    "",
    "Use /link <userId>" + (LINK_SECRET ? " <linkSecret>" : "") + " to connect this chat.",
    "Use /unlink to disconnect.",
  ];
  await ctx.reply(parts.join("\n"));
});

// /link <userId> [linkSecret]
bot.command("link", async (ctx) => {
  const args = ctx.match.split(/\s+/).filter(Boolean);
  const userId = Number(args[0]);

  if (!args[0] || !Number.isInteger(userId) || userId <= 0) {
    await ctx.reply(
      "Usage: /link <userId>" + (LINK_SECRET ? " <linkSecret>" : ""),
    );
    return;
  }

  const secret = args[1];

  try {
    const result = await animaApi.linkTelegram(ctx.chat.id, userId, secret);
    await ctx.reply(
      `Linked to user ${result.userId}. You can now chat with ANIMA here.`,
    );
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Unknown error";
    if (msg.includes("403")) {
      await ctx.reply("Invalid link secret.");
    } else if (msg.includes("404")) {
      await ctx.reply(`User ${userId} not found.`);
    } else {
      console.error("[telegram] link failed:", msg);
      await ctx.reply("Failed to link. Check logs for details.");
    }
  }
});

// /unlink
bot.command("unlink", async (ctx) => {
  try {
    await animaApi.unlinkTelegram(ctx.chat.id);
    await ctx.reply("Chat unlinked.");
  } catch (err) {
    console.error("[telegram] unlink failed:", err);
    await ctx.reply("Failed to unlink.");
  }
});

// Regular messages
bot.on("message:text", async (ctx) => {
  const chatId = ctx.chat.id;
  const text = ctx.message.text;

  const userId = await animaApi.lookupTelegram(chatId);
  if (userId === null) {
    await ctx.reply(
      "This chat is not linked. Use /link <userId>" +
        (LINK_SECRET ? " <linkSecret>" : "") +
        " to connect.",
    );
    return;
  }

  await ctx.replyWithChatAction("typing");

  try {
    const result = await animaApi.chat(text, userId);
    const chunks = splitMessage(result.response || "[empty response]");
    for (const chunk of chunks) {
      await ctx.reply(chunk);
    }
  } catch (err) {
    console.error("[telegram] chat failed:", err);
    await ctx.reply("Something went wrong. Please try again.");
  }
});
