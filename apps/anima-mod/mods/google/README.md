# Google Integration Mod

Provides Gmail and Calendar capabilities for ANIMA.

## Features

- **Gmail**: Search emails, read messages, send emails
- **Calendar**: List events, create events

## Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing)
3. Enable APIs:
   - Gmail API
   - Google Calendar API
4. Go to **Credentials → Create OAuth client ID**
5. Choose **Desktop app** (or Web application)
6. Add `http://127.0.0.1:3034/google/callback` as an authorized redirect URI
7. Copy the **Client ID** and **Client Secret** into the mod config

## Connecting an Account

Once the mod is configured:

1. Open `http://localhost:3034/google/auth-url?userId=<ANIMA_USER_ID>` in a browser
2. Authorize with Google
3. You'll be redirected back with a success message

Or use the Mods UI in the ANIMA desktop app to trigger the flow.

## Tool Endpoints

The cognitive core calls these endpoints to execute Google tools:

- `POST /google/gmail/search` — Search emails
- `POST /google/gmail/read` — Read a specific email
- `POST /google/gmail/send` — Send an email
- `POST /google/calendar/events` — List calendar events
- `POST /google/calendar/events/create` — Create a calendar event

All endpoints require `userId` in the request body.

## Connecting an Account

Once the mod is enabled and configured:

1. Get the auth URL: `GET http://localhost:3034/google/auth-url?userId=<YOUR_ANIMA_USER_ID>`
2. Open the returned `authUrl` in a browser
3. Authorize with Google
4. You'll be redirected back with a success message

To check status: `GET http://localhost:3034/google/status?userId=<YOUR_ANIMA_USER_ID>`

To disconnect: `POST http://localhost:3034/google/disconnect` with body `{ "userId": <YOUR_ANIMA_USER_ID> }`
