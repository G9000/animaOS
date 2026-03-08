import type { Context } from "hono";
import { eq } from "drizzle-orm";
import { db } from "../../db";
import { userKeys, users } from "../../db/schema";
import { createWrappedDek, unwrapDek } from "../../lib/auth-crypto";
import { ensureDefaultSoul } from "../../lib/user-soul";
import {
  createUnlockSession,
  revokeUnlockSession,
  resolveUnlockSession,
} from "../../lib/unlock-session";
import { readUnlockToken } from "../../lib/require-unlock";
import { readMemory, writeMemory } from "../../memory";

async function ensureMemoryFile(
  section: string,
  userId: number,
  filename: string,
  content: string,
  tags: string[],
): Promise<void> {
  try {
    await readMemory(section, userId, filename);
  } catch {
    await writeMemory(section, userId, filename, content, {
      category: section,
      tags,
      source: "system",
    });
  }
}

async function ensureDefaultMemory(userId: number, name: string): Promise<void> {
  await Promise.all([
    ensureMemoryFile(
      "user",
      userId,
      "facts",
      `# User Facts\n\n- Name: ${name}\n- Local-first owner of this vault.`,
      ["profile", "bootstrap"],
    ),
    ensureMemoryFile(
      "user",
      userId,
      "preferences",
      "# Preferences\n\n- Add your preferences here.\n",
      ["preferences", "bootstrap"],
    ),
    ensureMemoryFile(
      "user",
      userId,
      "current-focus",
      "# Current Focus\n\n- [ ] Define your current focus\n",
      ["focus", "bootstrap"],
    ),
    ensureMemoryFile(
      "knowledge",
      userId,
      "notes",
      "# Notes\n\n- Capture useful long-term notes here.\n",
      ["knowledge", "bootstrap"],
    ),
  ]);
}

async function ensureUserSeedData(userId: number, name: string): Promise<void> {
  await Promise.all([ensureDefaultSoul(userId), ensureDefaultMemory(userId, name)]);
}

export async function register(c: Context) {
  const { username, password, name } = c.req.valid("json" as never);
  const normalizedUsername = String(username).trim().toLowerCase();

  const [existing] = await db
    .select({ id: users.id })
    .from(users)
    .where(eq(users.username, normalizedUsername));
  if (existing) return c.json({ error: "Username already taken" }, 409);

  const hashedPassword = await Bun.password.hash(password);
  const { dek, record } = createWrappedDek(password);
  const [user] = await db
    .insert(users)
    .values({
      username: normalizedUsername,
      password: hashedPassword,
      name,
    })
    .returning({
      id: users.id,
      username: users.username,
      name: users.name,
      createdAt: users.createdAt,
    });

  await db.insert(userKeys).values({
    userId: user.id,
    kdfSalt: record.kdfSalt,
    kdfTimeCost: record.kdfTimeCost,
    kdfMemoryCostKib: record.kdfMemoryCostKib,
    kdfParallelism: record.kdfParallelism,
    kdfKeyLength: record.kdfKeyLength,
    wrapIv: record.wrapIv,
    wrapTag: record.wrapTag,
    wrappedDek: record.wrappedDek,
  });

  const unlockToken = createUnlockSession(user.id, dek);
  await ensureUserSeedData(user.id, user.name);
  return c.json({ ...user, unlockToken }, 201);
}

export async function login(c: Context) {
  const { username, password } = c.req.valid("json" as never);
  const normalizedUsername = String(username).trim().toLowerCase();

  const [user] = await db.select().from(users).where(eq(users.username, normalizedUsername));
  if (!user) return c.json({ error: "Invalid credentials" }, 401);

  const valid = await Bun.password.verify(password, user.password);
  if (!valid) return c.json({ error: "Invalid credentials" }, 401);

  const [keyRow] = await db
    .select()
    .from(userKeys)
    .where(eq(userKeys.userId, user.id));
  if (!keyRow) {
    return c.json({ error: "User key material is missing." }, 500);
  }

  let dek: Buffer;
  try {
    dek = unwrapDek(password, {
      kdfSalt: keyRow.kdfSalt,
      kdfTimeCost: keyRow.kdfTimeCost,
      kdfMemoryCostKib: keyRow.kdfMemoryCostKib,
      kdfParallelism: keyRow.kdfParallelism,
      kdfKeyLength: keyRow.kdfKeyLength,
      wrapIv: keyRow.wrapIv,
      wrapTag: keyRow.wrapTag,
      wrappedDek: keyRow.wrappedDek,
    });
  } catch {
    return c.json({ error: "Invalid credentials" }, 401);
  }

  const unlockToken = createUnlockSession(user.id, dek);
  await ensureUserSeedData(user.id, user.name);
  return c.json({
    id: user.id,
    username: user.username,
    name: user.name,
    unlockToken,
    message: "Login successful",
  });
}

export async function me(c: Context) {
  const token = readUnlockToken(c);
  const session = resolveUnlockSession(token);
  if (!session) return c.json({ error: "Session locked." }, 401);

  const [user] = await db
    .select({
      id: users.id,
      username: users.username,
      name: users.name,
      createdAt: users.createdAt,
    })
    .from(users)
    .where(eq(users.id, session.userId));

  if (!user) return c.json({ error: "User not found" }, 404);
  return c.json(user);
}

export async function logout(c: Context) {
  revokeUnlockSession(readUnlockToken(c));
  return c.json({ success: true });
}
