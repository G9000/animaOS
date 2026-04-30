/**
 * Gmail API helpers for the Google mod.
 */

import { Buffer } from "node:buffer";

interface GmailMessageHeader {
  name: string;
  value: string;
}

interface GmailMessagePart {
  mimeType?: string;
  body?: { data?: string };
  parts?: GmailMessagePart[];
}

interface GmailMessage {
  id: string;
  threadId: string;
  snippet: string;
  payload?: {
    headers: GmailMessageHeader[];
    parts?: GmailMessagePart[];
    body?: { data?: string };
  };
  internalDate: string;
}

interface GmailListResponse {
  messages?: Array<{ id: string; threadId: string }>;
  nextPageToken?: string;
  resultSizeEstimate?: number;
}

function extractHeader(msg: GmailMessage, name: string): string {
  const h = msg.payload?.headers.find(
    (h) => h.name.toLowerCase() === name.toLowerCase(),
  );
  return h?.value ?? "";
}

function decodeBase64Url(data: string): string {
  const normalized = data.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(
    normalized.length + ((4 - (normalized.length % 4)) % 4),
    "=",
  );
  return Buffer.from(padded, "base64").toString("utf8");
}

function findPartBody(parts: GmailMessagePart[] | undefined, mimeType: string): string | null {
  for (const part of parts ?? []) {
    if (part.mimeType === mimeType && part.body?.data) {
      try {
        return decodeBase64Url(part.body.data);
      } catch {
        return "";
      }
    }

    const nested = findPartBody(part.parts, mimeType);
    if (nested !== null) {
      return nested;
    }
  }

  return null;
}

function decodeBody(msg: GmailMessage): string {
  const parts = msg.payload?.parts;
  if (parts && parts.length > 0) {
    const textBody = findPartBody(parts, "text/plain");
    if (textBody !== null) {
      return textBody;
    }

    const htmlBody = findPartBody(parts, "text/html");
    if (htmlBody !== null) {
      return htmlBody;
    }
  }
  if (msg.payload?.body?.data) {
    try {
      return decodeBase64Url(msg.payload.body.data);
    } catch {
      return "";
    }
  }
  return msg.snippet ?? "";
}

export async function searchGmail(
  accessToken: string,
  query: string,
  maxResults: number,
): Promise<string> {
  if (!query.trim()) {
    throw new Error("Query cannot be empty.");
  }
  const params = new URLSearchParams({
    q: query,
    maxResults: String(Math.min(maxResults, 20)),
  });

  const res = await fetch(
    `https://gmail.googleapis.com/gmail/v1/users/me/messages?${params}`,
    {
      headers: { Authorization: `Bearer ${accessToken}` },
    },
  );

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Gmail search failed: ${res.status} ${text}`);
  }

  const data = (await res.json()) as GmailListResponse;
  const messages = data.messages ?? [];

  if (messages.length === 0) {
    return "No emails found matching your query.";
  }

  // Fetch details for each message
  const details: string[] = [];
  for (const m of messages.slice(0, maxResults)) {
    const detailRes = await fetch(
      `https://gmail.googleapis.com/gmail/v1/users/me/messages/${m.id}?format=full`,
      {
        headers: { Authorization: `Bearer ${accessToken}` },
      },
    );
    if (!detailRes.ok) continue;

    const msg = (await detailRes.json()) as GmailMessage;
    const from = extractHeader(msg, "From");
    const subject = extractHeader(msg, "Subject");
    const date = new Date(Number(msg.internalDate)).toISOString();
    const body = decodeBody(msg).slice(0, 500);

    details.push(
      `---\nID: ${msg.id}\nFrom: ${from}\nSubject: ${subject}\nDate: ${date}\n\n${body}${body.length >= 500 ? "..." : ""}`,
    );
  }

  return `Found ${messages.length} email(s):\n${details.join("\n")}`;
}

export async function readGmail(
  accessToken: string,
  messageId: string,
): Promise<string> {
  const res = await fetch(
    `https://gmail.googleapis.com/gmail/v1/users/me/messages/${messageId}?format=full`,
    {
      headers: { Authorization: `Bearer ${accessToken}` },
    },
  );

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Gmail read failed: ${res.status} ${text}`);
  }

  const msg = (await res.json()) as GmailMessage;
  const from = extractHeader(msg, "From");
  const to = extractHeader(msg, "To");
  const subject = extractHeader(msg, "Subject");
  const date = new Date(Number(msg.internalDate)).toISOString();
  const body = decodeBody(msg);

  return `From: ${from}\nTo: ${to}\nSubject: ${subject}\nDate: ${date}\n\n${body}`;
}

export async function sendGmail(
  accessToken: string,
  to: string,
  subject: string,
  body: string,
): Promise<string> {
  const raw = [
    `To: ${to}`,
    `Subject: ${subject}`,
    "Content-Type: text/plain; charset=utf-8",
    "MIME-Version: 1.0",
    "",
    body,
  ].join("\r\n");

  const encoded = Buffer.from(raw, "utf8")
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

  const res = await fetch(
    "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ raw: encoded }),
    },
  );

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Gmail send failed: ${res.status} ${text}`);
  }

  const data = (await res.json()) as { id?: string };
  return `Email sent successfully (message ID: ${data.id ?? "unknown"})`;
}
