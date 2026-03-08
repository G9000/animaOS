# Discord Integration

ANIMA supports Discord chat through a webhook relay endpoint:

- `POST /api/discord/webhook`

## Required environment variables

- `DISCORD_BOT_TOKEN`: Bot token for sending replies.

## Optional environment variables

- `DISCORD_WEBHOOK_SECRET`: If set, requests must include `X-Discord-Webhook-Secret`.
- `DISCORD_LINK_SECRET`: If set, users must include this in `/link <userId> <linkSecret>`.
- `DISCORD_GATEWAY_RELAY`: Enable built-in gateway relay when `true`/`1`.
- `DISCORD_GATEWAY_RELAY_URL`: Target webhook URL (default: `http://127.0.0.1:3031/api/discord/webhook`).
- `DISCORD_GATEWAY_INTENTS`: Optional integer intents override.

## Link flow (from Discord channel)

1. Send `/start`
2. Link with:
   - `/link <userId>` (or `/link <userId> <linkSecret>` when enabled)
3. Send normal messages; they are forwarded to ANIMA
4. Unlink with `/unlink`

## Payload shape expected by webhook

The endpoint accepts either:

1. A direct message-like payload:
```json
{
  "channel_id": "123456789012345678",
  "content": "hello",
  "author": { "bot": false }
}
```

2. Or wrapped under `message`:
```json
{
  "message": {
    "channel_id": "123456789012345678",
    "content": "hello",
    "author": { "bot": false }
  }
}
```

Messages from bot authors are ignored.

## Built-in gateway relay

When `DISCORD_GATEWAY_RELAY=true`, ANIMA's API process also opens a Discord
Gateway connection and forwards `MESSAGE_CREATE` events into
`/api/discord/webhook` automatically.
