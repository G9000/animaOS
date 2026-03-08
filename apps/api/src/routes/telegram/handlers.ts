// Telegram route handlers

import type { Context } from "hono";
import { eq, or } from "drizzle-orm";
import { db } from "../../db";
import * as schema from "../../db/schema";
import { handleChannelMessage } from "../../channels";

interface TelegramMessage {
  chat?: { id?: number };
  text?: string;
}

interface TelegramUpdate {
  message?: TelegramMessage;
}

function getTelegramConfig() {
  const token = process.env.TELEGRAM_BOT_TOKEN;
  const webhookSecret = process.env.TELEGRAM_WEBHOOK_SECRET;
  const linkSecret = process.env.TELEGRAM_LINK_SECRET;
  return { token, webhookSecret, linkSecret };
}

async function sendTelegramMessage(
  token: string,
  chatId: number,
  text: string,
): Promise<void> {
  const maxLength = 4096;
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += maxLength) {
    chunks.push(text.slice(i, i + maxLength));
  }

  for (const chunk of chunks) {
    const res = await fetch(
      `https://api.telegram.org/bot${token}/sendMessage`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          chat_id: chatId,
          text: chunk || "[empty response]",
        }),
      },
    );

    if (!res.ok) {
      const body = await res.text();
      throw new Error(`Telegram sendMessage failed: ${res.status} ${body}`);
    }
  }
}

function parseLinkCommand(text: string): { userId?: number; secret?: string } {
  const parts = text.trim().split(/\s+/);
  if (parts.length < 2) return {};
  const userId = Number(parts[1]);
  if (!Number.isInteger(userId) || userId <= 0) return {};
  return { userId, secret: parts[2] };
}

async function handleLinkCommand(
  token: string,
  chatId: number,
  text: string,
  linkSecret?: string,
) {
  const { userId, secret } = parseLinkCommand(text);
  if (!userId) {
    await sendTelegramMessage(
      token,
      chatId,
      "Usage: /link <userId>" + (linkSecret ? " <linkSecret>" : ""),
    );
    return;
  }

  if (linkSecret && secret !== linkSecret) {
    await sendTelegramMessage(token, chatId, "Invalid link secret.");
    return;
  }

  const [user] = await db
    .select({ id: schema.users.id, name: schema.users.name })
    .from(schema.users)
    .where(eq(schema.users.id, userId))
    .limit(1);

  if (!user) {
    await sendTelegramMessage(token, chatId, `User ${userId} not found.`);
    return;
  }

  await db
    .delete(schema.telegramLinks)
    .where(
      or(
        eq(schema.telegramLinks.chatId, chatId),
        eq(schema.telegramLinks.userId, user.id),
      ),
    );

  await db.insert(schema.telegramLinks).values({ chatId, userId: user.id });

  await sendTelegramMessage(
    token,
    chatId,
    `Linked to ${user.name} (userId=${user.id}). You can now chat with ANIMA here.`,
  );
}

async function handleUnlinkCommand(token: string, chatId: number) {
  await db
    .delete(schema.telegramLinks)
    .where(eq(schema.telegramLinks.chatId, chatId));
  await sendTelegramMessage(token, chatId, "Telegram chat unlinked.");
}

async function handleChatMessage(token: string, chatId: number, text: string) {
  const [link] = await db
    .select()
    .from(schema.telegramLinks)
    .where(eq(schema.telegramLinks.chatId, chatId))
    .limit(1);

  if (!link) {
    await sendTelegramMessage(
      token,
      chatId,
      "This chat is not linked yet. Use /link <userId> to connect it.",
    );
    return;
  }

  const result = await handleChannelMessage({
    channel: "telegram",
    userId: link.userId,
    text,
    metadata: { chatId },
  });
  await sendTelegramMessage(token, chatId, result.text);
}

// POST /telegram/webhook
export async function webhook(c: Context) {
  const { token, webhookSecret, linkSecret } = getTelegramConfig();

  if (!token) {
    return c.json({ error: "TELEGRAM_BOT_TOKEN is not configured" }, 503);
  }

  if (webhookSecret) {
    const incomingSecret =
      c.req.header("X-Telegram-Bot-Api-Secret-Token") || "";
    if (incomingSecret !== webhookSecret) {
      return c.json({ error: "invalid webhook secret" }, 401);
    }
  }

  const update = (await c.req
    .json()
    .catch(() => null)) as TelegramUpdate | null;
  if (!update?.message?.chat?.id) {
    return c.json({ ok: true });
  }

  const chatId = update.message.chat.id;
  const text = update.message.text?.trim();
  if (!text) {
    return c.json({ ok: true });
  }

  queueMicrotask(async () => {
    try {
      if (text.startsWith("/start")) {
        await sendTelegramMessage(
          token,
          chatId,
          "ANIMA is online.\nUse /link <userId>" +
            (linkSecret ? " <linkSecret>" : "") +
            " to connect this Telegram chat.",
        );
        return;
      }

      if (text.startsWith("/link")) {
        await handleLinkCommand(token, chatId, text, linkSecret);
        return;
      }

      if (text.startsWith("/unlink")) {
        await handleUnlinkCommand(token, chatId);
        return;
      }

      await handleChatMessage(token, chatId, text);
    } catch (err) {
      console.error("[telegram] failed:", (err as Error).message);
      try {
        await sendTelegramMessage(
          token,
          chatId,
          "Something went wrong while handling your message.",
        );
      } catch {
        // Ignore follow-up Telegram failures.
      }
    }
  });

  return c.json({ ok: true });
}
