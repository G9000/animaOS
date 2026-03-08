import type { Context } from "hono";
import { handleChannelMessage } from "../../channels";

// POST /channel/message
export async function sendChannelMessage(c: Context) {
  const { userId, message } = c.req.valid("json" as never);

  try {
    const result = await handleChannelMessage({
      channel: "webhook",
      userId,
      text: message,
    });

    return c.json({
      response: result.text,
      model: result.model || "unknown",
      provider: result.provider || "unknown",
      toolsUsed: result.toolsUsed || [],
    });
  } catch (err: any) {
    return c.json({ error: err.message }, 500);
  }
}
