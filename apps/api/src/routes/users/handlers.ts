// User route handlers

import type { Context } from "hono";
import { eq } from "drizzle-orm";
import { readdir, rm } from "node:fs/promises";
import { join } from "node:path";
import { db } from "../../db";
import { userKeys, users } from "../../db/schema";
import { requireUnlockedUser } from "../../lib/require-unlock";
import { MEMORY_ROOT, SOUL_DIR } from "../../lib/runtime-paths";

// POST /users
export async function createUser(c: Context) {
  const data = c.req.valid("json" as never);
  const [user] = await db.insert(users).values(data).returning();
  return c.json(user, 201);
}

// GET /users
export async function listUsers(c: Context) {
  const allUsers = await db.select().from(users);
  return c.json(allUsers);
}

// GET /users/:id
export async function getUser(c: Context) {
  const id = Number(c.req.param("id"));
  const auth = requireUnlockedUser(c, id);
  if (!auth.ok) return auth.response;

  const [user] = await db.select().from(users).where(eq(users.id, id));
  if (!user) return c.json({ error: "User not found" }, 404);
  return c.json(user);
}

// PUT /users/:id
export async function updateUser(c: Context) {
  const id = Number(c.req.param("id"));
  const auth = requireUnlockedUser(c, id);
  if (!auth.ok) return auth.response;

  const data = c.req.valid("json" as never) as Record<string, unknown>;
  const [user] = await db
    .update(users)
    .set({ ...data, updatedAt: new Date().toISOString() })
    .where(eq(users.id, id))
    .returning();
  if (!user) return c.json({ error: "User not found" }, 404);
  return c.json(user);
}

// DELETE /users/:id
export async function deleteUser(c: Context) {
  const id = Number(c.req.param("id"));
  const auth = requireUnlockedUser(c, id);
  if (!auth.ok) return auth.response;

  await db.delete(userKeys).where(eq(userKeys.userId, id));
  const [user] = await db.delete(users).where(eq(users.id, id)).returning();
  if (!user) return c.json({ error: "User not found" }, 404);

  // Remove user filesystem data (memory + per-user soul file)
  try {
    const sections = await readdir(MEMORY_ROOT, { withFileTypes: true });
    await Promise.all(
      sections
        .filter((entry) => entry.isDirectory())
        .map((entry) =>
          rm(join(MEMORY_ROOT, entry.name, String(id)), {
            recursive: true,
            force: true,
          }),
        ),
    );
  } catch {
    // ignore filesystem cleanup failures
  }

  await rm(join(SOUL_DIR, `${id}.soul.md`), { force: true });
  return c.json({ message: "User deleted" });
}
