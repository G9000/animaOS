// Soul route handlers

import type { Context } from "hono";
import { invalidateSoulCache } from "../../agent/graph";
import { readUserSoul, writeUserSoul } from "../../lib/user-soul";
import { requireUnlockedUser } from "../../lib/require-unlock";

// GET /soul
export async function getSoul(c: Context) {
  const userId = Number(c.req.param("userId"));
  if (!Number.isFinite(userId)) return c.json({ error: "Invalid userId" }, 400);

  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  try {
    const { content, path } = await readUserSoul(userId);
    return c.json({ content, path });
  } catch {
    return c.json({ content: "", path: "" }, 200);
  }
}

// PUT /soul
export async function updateSoul(c: Context) {
  const userId = Number(c.req.param("userId"));
  if (!Number.isFinite(userId)) return c.json({ error: "Invalid userId" }, 400);

  const auth = requireUnlockedUser(c, userId);
  if (!auth.ok) return auth.response;

  const { content } = c.req.valid("json" as never);

  try {
    const { path } = await writeUserSoul(userId, content);
    invalidateSoulCache();
    return c.json({ status: "saved", path });
  } catch (err: any) {
    return c.json({ error: err.message }, 500);
  }
}
