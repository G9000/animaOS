/**
 * Google OAuth 2.0 flow helpers for the Google mod.
 */

import type { ModContext } from "../../src/core/types.js";

const GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth";
const GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token";
const GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke";

export interface GoogleTokens {
  email: string;
  accessToken: string;
  refreshToken: string;
  expiresAt: number; // timestamp ms
}

export interface AuthState {
  userId: number;
  createdAt: number;
}

export function buildAuthUrl(
  clientId: string,
  redirectUri: string,
  state: string,
): string {
  const scopes = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
  ];

  const params = new URLSearchParams({
    client_id: clientId,
    redirect_uri: redirectUri,
    response_type: "code",
    scope: scopes.join(" "),
    access_type: "offline",
    prompt: "consent",
    state,
  });

  return `${GOOGLE_AUTH_URL}?${params.toString()}`;
}

export async function exchangeCode(
  clientId: string,
  clientSecret: string,
  redirectUri: string,
  code: string,
): Promise<Omit<GoogleTokens, "email">> {
  const body = new URLSearchParams({
    client_id: clientId,
    client_secret: clientSecret,
    redirect_uri: redirectUri,
    grant_type: "authorization_code",
    code,
  });

  const res = await fetch(GOOGLE_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Token exchange failed: ${res.status} ${text}`);
  }

  const data = (await res.json()) as Record<string, unknown>;

  if (data.error) {
    throw new Error(`Google token error: ${data.error} — ${data.error_description || ""}`);
  }

  const accessToken = String(data.access_token ?? "");
  const refreshToken = String(data.refresh_token ?? "");
  const expiresIn = Number(data.expires_in ?? 3600);

  if (!accessToken) {
    throw new Error("No access_token in Google response");
  }

  return {
    accessToken,
    refreshToken,
    expiresAt: Date.now() + expiresIn * 1000,
  };
}

export async function refreshAccessToken(
  clientId: string,
  clientSecret: string,
  refreshToken: string,
): Promise<{ accessToken: string; expiresAt: number }> {
  const body = new URLSearchParams({
    client_id: clientId,
    client_secret: clientSecret,
    grant_type: "refresh_token",
    refresh_token: refreshToken,
  });

  const res = await fetch(GOOGLE_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Token refresh failed: ${res.status} ${text}`);
  }

  const data = (await res.json()) as Record<string, unknown>;
  const accessToken = String(data.access_token ?? "");
  const expiresIn = Number(data.expires_in ?? 3600);

  if (!accessToken) {
    throw new Error("No access_token in refresh response");
  }

  return {
    accessToken,
    expiresAt: Date.now() + expiresIn * 1000,
  };
}

export async function revokeToken(token: string): Promise<void> {
  await fetch(`${GOOGLE_REVOKE_URL}?token=${encodeURIComponent(token)}`, {
    method: "POST",
  });
}

export async function getUserEmail(accessToken: string): Promise<string> {
  const res = await fetch("https://www.googleapis.com/oauth2/v2/userinfo", {
    headers: { Authorization: `Bearer ${accessToken}` },
  });

  if (!res.ok) {
    throw new Error(`Failed to fetch userinfo: ${res.status}`);
  }

  const data = (await res.json()) as Record<string, unknown>;
  return String(data.email ?? "");
}

export async function getValidAccessToken(
  ctx: ModContext,
  clientId: string,
  clientSecret: string,
  userId: number,
): Promise<string | null> {
  const tokens = await ctx.store.get<GoogleTokens>(`google:tokens:${userId}`);
  if (!tokens) return null;

  // Refresh if expiring in < 5 minutes
  if (Date.now() > tokens.expiresAt - 5 * 60 * 1000) {
    if (!tokens.refreshToken) {
      await ctx.store.delete(`google:tokens:${userId}`);
      return null;
    }
    try {
      const refreshed = await refreshAccessToken(
        clientId,
        clientSecret,
        tokens.refreshToken,
      );
      const updated: GoogleTokens = {
        ...tokens,
        accessToken: refreshed.accessToken,
        expiresAt: refreshed.expiresAt,
      };
      await ctx.store.set(`google:tokens:${userId}`, updated);
      return refreshed.accessToken;
    } catch {
      await ctx.store.delete(`google:tokens:${userId}`);
      return null;
    }
  }

  return tokens.accessToken;
}

export function generateState(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
