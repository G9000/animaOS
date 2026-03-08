import { randomBytes } from "node:crypto";

const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;

type Session = {
  userId: number;
  dek: Buffer;
  expiresAt: number;
};

const sessionsByToken = new Map<string, Session>();
const latestDekByUser = new Map<number, Buffer>();

function now(): number {
  return Date.now();
}

function purgeExpired(): void {
  const current = now();
  for (const [token, session] of sessionsByToken.entries()) {
    if (session.expiresAt <= current) sessionsByToken.delete(token);
  }
}

export function createUnlockSession(userId: number, dek: Buffer): string {
  purgeExpired();
  const token = randomBytes(32).toString("base64url");
  sessionsByToken.set(token, {
    userId,
    dek,
    expiresAt: now() + SESSION_TTL_MS,
  });
  latestDekByUser.set(userId, dek);
  return token;
}

export function resolveUnlockSession(token: string | undefined): Session | null {
  if (!token) return null;
  purgeExpired();
  const session = sessionsByToken.get(token);
  if (!session) return null;
  if (session.expiresAt <= now()) {
    sessionsByToken.delete(token);
    return null;
  }
  return session;
}

export function revokeUnlockSession(token: string | undefined): void {
  if (!token) return;
  sessionsByToken.delete(token);
}

export function getActiveDek(userId: number): Buffer | null {
  return latestDekByUser.get(userId) ?? null;
}
