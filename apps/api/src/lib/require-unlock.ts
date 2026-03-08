import type { Context } from "hono";
import { resolveUnlockSession } from "./unlock-session";

export function readUnlockToken(c: Context): string | undefined {
  return c.req.header("x-anima-unlock")?.trim() || undefined;
}

export function requireUnlockedUser(c: Context, userId: number): { ok: true } | { ok: false; response: Response } {
  const token = readUnlockToken(c);
  const session = resolveUnlockSession(token);

  if (!session) {
    return {
      ok: false,
      response: c.json({ error: "Session locked. Please sign in again." }, 401),
    };
  }

  if (session.userId !== userId) {
    return {
      ok: false,
      response: c.json({ error: "Session user mismatch." }, 403),
    };
  }

  return { ok: true };
}
