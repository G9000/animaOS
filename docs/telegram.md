# Telegram Integration

This project supports Telegram chat through the legacy `apps/api` webhook
surface. The current Python server in `apps/server` does not expose this route.

Endpoint:

- `POST /api/telegram/webhook`

## Required environment variables

- `TELEGRAM_BOT_TOKEN`: Bot token from `@BotFather`

## Optional environment variables

- `TELEGRAM_WEBHOOK_SECRET`: If set, webhook requests must include matching `X-Telegram-Bot-Api-Secret-Token`
- `TELEGRAM_LINK_SECRET`: If set, users must include this in `/link <userId> <linkSecret>`

## Link flow (from Telegram)

1. Open your bot chat and send `/start`
2. Link your account with:
   - `/link <userId>` (or `/link <userId> <linkSecret>` when enabled)
3. Send normal messages; they are forwarded to ANIMA agent
4. Unlink anytime with `/unlink`

## Set webhook

Replace placeholders and run:

```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -d "url=https://<your-public-api-host>/api/telegram/webhook" \
  -d "secret_token=<TELEGRAM_WEBHOOK_SECRET>"
```

If you don't use a webhook secret, omit `secret_token`.
