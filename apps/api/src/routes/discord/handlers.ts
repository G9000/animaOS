import type { Context } from "hono";
import { eq, or } from "drizzle-orm";
import { db } from "../../db";
import * as schema from "../../db/schema";
import { handleChannelMessage } from "../../channels";

interface DiscordAuthor {
  bot?: boolean;
}

interface DiscordMessageLike {
  channel_id?: string;
  content?: string;
  author?: DiscordAuthor;
}

interface DiscordWebhookEvent {
  type?: string;
  message?: DiscordMessageLike;
  channel_id?: string;
  content?: string;
  author?: DiscordAuthor;
}

function getDiscordConfig() {
  const token = process.env.DISCORD_BOT_TOKEN;
  const webhookSecret = process.env.DISCORD_WEBHOOK_SECRET;
  const linkSecret = process.env.DISCORD_LINK_SECRET;
  return { token, webhookSecret, linkSecret };
}

async function sendDiscordMessage(
  token: string,
  channelId: string,
  text: string,
): Promise<void> {
  const maxLength = 2000;
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += maxLength) {
    chunks.push(text.slice(i, i + maxLength));
  }

  for (const chunk of chunks) {
    const res = await fetch(
      `https://discord.com/api/v10/channels/${channelId}/messages`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bot ${token}`,
        },
        body: JSON.stringify({ content: chunk || "[empty response]" }),
      },
    );

    if (!res.ok) {
      const body = await res.text();
      throw new Error(`Discord send message failed: ${res.status} ${body}`);
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

function readMessage(event: DiscordWebhookEvent): {
  channelId?: string;
  content?: string;
  fromBot: boolean;
} {
  const msg = event.message || event;
  return {
    channelId: msg.channel_id,
    content: msg.content?.trim(),
    fromBot: !!msg.author?.bot,
  };
}

async function handleLinkCommand(
  token: string,
  channelId: string,
  text: string,
  linkSecret?: string,
) {
  const { userId, secret } = parseLinkCommand(text);

  if (!userId) {
    await sendDiscordMessage(
      token,
      channelId,
      "Usage: /link <userId>" + (linkSecret ? " <linkSecret>" : ""),
    );
    return;
  }

  if (linkSecret && secret !== linkSecret) {
    await sendDiscordMessage(token, channelId, "Invalid link secret.");
    return;
  }

  const [user] = await db
    .select({ id: schema.users.id, name: schema.users.name })
    .from(schema.users)
    .where(eq(schema.users.id, userId))
    .limit(1);

  if (!user) {
    await sendDiscordMessage(token, channelId, `User ${userId} not found.`);
    return;
  }

  await db
    .delete(schema.discordLinks)
    .where(
      or(
        eq(schema.discordLinks.channelId, channelId),
        eq(schema.discordLinks.userId, user.id),
      ),
    );

  await db.insert(schema.discordLinks).values({ channelId, userId: user.id });

  await sendDiscordMessage(
    token,
    channelId,
    `Linked to ${user.name} (userId=${user.id}). You can now chat with ANIMA here.`,
  );
}

async function handleUnlinkCommand(token: string, channelId: string) {
  await db
    .delete(schema.discordLinks)
    .where(eq(schema.discordLinks.channelId, channelId));
  await sendDiscordMessage(token, channelId, "Discord channel unlinked.");
}

async function handleChatMessage(token: string, channelId: string, text: string) {
  const [link] = await db
    .select()
    .from(schema.discordLinks)
    .where(eq(schema.discordLinks.channelId, channelId))
    .limit(1);

  if (!link) {
    await sendDiscordMessage(
      token,
      channelId,
      "This channel is not linked yet. Use /link <userId> to connect it.",
    );
    return;
  }

  const result = await handleChannelMessage({
    channel: "discord",
    userId: link.userId,
    text,
    metadata: { channelId },
  });

  await sendDiscordMessage(token, channelId, result.text);
}

// POST /discord/webhook
export async function webhook(c: Context) {
  const { token, webhookSecret, linkSecret } = getDiscordConfig();

  if (!token) {
    return c.json({ error: "DISCORD_BOT_TOKEN is not configured" }, 503);
  }

  if (webhookSecret) {
    const incomingSecret = c.req.header("X-Discord-Webhook-Secret") || "";
    if (incomingSecret !== webhookSecret) {
      return c.json({ error: "invalid webhook secret" }, 401);
    }
  }

  const event = (await c.req
    .json()
    .catch(() => null)) as DiscordWebhookEvent | null;

  if (!event) {
    return c.json({ ok: true });
  }

  const { channelId, content, fromBot } = readMessage(event);
  if (!channelId || !content || fromBot) {
    return c.json({ ok: true });
  }

  queueMicrotask(async () => {
    try {
      if (content.startsWith("/start")) {
        await sendDiscordMessage(
          token,
          channelId,
          "ANIMA is online.\nUse /link <userId>" +
            (linkSecret ? " <linkSecret>" : "") +
            " to connect this Discord channel.",
        );
        return;
      }

      if (content.startsWith("/link")) {
        await handleLinkCommand(token, channelId, content, linkSecret);
        return;
      }

      if (content.startsWith("/unlink")) {
        await handleUnlinkCommand(token, channelId);
        return;
      }

      await handleChatMessage(token, channelId, content);
    } catch (err) {
      console.error("[discord] failed:", (err as Error).message);
      try {
        await sendDiscordMessage(
          token,
          channelId,
          "Something went wrong while handling your message.",
        );
      } catch {
        // Ignore follow-up Discord failures.
      }
    }
  });

  return c.json({ ok: true });
}
