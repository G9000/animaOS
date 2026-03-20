import { bot } from "./bot";

export async function setupTelegramWebhook(): Promise<void> {
  const webhookUrl = process.env.TELEGRAM_WEBHOOK_URL;
  const secretToken = process.env.TELEGRAM_WEBHOOK_SECRET;

  if (!webhookUrl) {
    console.warn(
      "[telegram] TELEGRAM_WEBHOOK_URL not set — skipping webhook registration",
    );
    return;
  }

  await bot.api.setWebhook(webhookUrl, {
    secret_token: secretToken,
  });

  console.log(`[telegram] Webhook registered: ${webhookUrl}`);
}
