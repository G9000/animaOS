// User route handlers

import type { Context } from "hono";
import { eq } from "drizzle-orm";
import { db } from "../../db";
import { userKeys, users } from "../../db/schema";
import { requireUnlockedUser } from "../../lib/require-unlock";

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
  return c.json({ message: "User deleted" });
}
