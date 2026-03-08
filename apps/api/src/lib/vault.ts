import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import { argon2id } from "@noble/hashes/argon2.js";
import { db } from "../db";
import {
  agentConfig,
  agentThreads,
  discordLinks,
  langgraphCheckpoints,
  langgraphWrites,
  messages,
  tasks,
  telegramLinks,
  users,
} from "../db/schema";
import { MEMORY_ROOT, SOUL_DIR } from "./runtime-paths";

const VAULT_VERSION = 1;
const ARGON2_TIME_COST = 3;
const ARGON2_MEMORY_COST_KIB = 64 * 1024;
const ARGON2_PARALLELISM = 1;
const KEY_LENGTH = 32;
const ALGO = "aes-256-gcm";
const IV_LENGTH = 12;

interface DatabaseSnapshot {
  users: Array<typeof users.$inferSelect>;
  messages: Array<typeof messages.$inferSelect>;
  agentConfig: Array<typeof agentConfig.$inferSelect>;
  telegramLinks: Array<typeof telegramLinks.$inferSelect>;
  discordLinks: Array<typeof discordLinks.$inferSelect>;
  tasks: Array<typeof tasks.$inferSelect>;
  agentThreads: Array<typeof agentThreads.$inferSelect>;
  langgraphCheckpoints: Array<typeof langgraphCheckpoints.$inferSelect>;
  langgraphWrites: Array<typeof langgraphWrites.$inferSelect>;
}

interface VaultPayload {
  version: number;
  createdAt: string;
  database: DatabaseSnapshot;
  memoryFiles: Record<string, string>;
  soulFiles: Record<string, string>;
}

interface VaultEnvelope {
  version: number;
  createdAt: string;
  payloadVersion: number;
  kdf: {
    name: "argon2id";
    timeCost: number;
    memoryCostKiB: number;
    parallelism: number;
    keyLength: number;
    salt: string;
  };
  cipher: {
    name: "aes-256-gcm";
    iv: string;
    tag: string;
  };
  ciphertext: string;
}

function deriveArgon2idKey(passphrase: string, salt: Buffer): Buffer {
  const passphraseBytes = new TextEncoder().encode(passphrase);
  const key = argon2id(passphraseBytes, salt, {
    t: ARGON2_TIME_COST,
    m: ARGON2_MEMORY_COST_KIB,
    p: ARGON2_PARALLELISM,
    dkLen: KEY_LENGTH,
  });
  return Buffer.from(key);
}

function encryptString(plaintext: string, passphrase: string): VaultEnvelope {
  const salt = randomBytes(16);
  const iv = randomBytes(IV_LENGTH);
  const key = deriveArgon2idKey(passphrase, salt);
  const cipher = createCipheriv(ALGO, key, iv);

  const encrypted = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();

  return {
    version: VAULT_VERSION,
    createdAt: new Date().toISOString(),
    payloadVersion: VAULT_VERSION,
    kdf: {
      name: "argon2id",
      timeCost: ARGON2_TIME_COST,
      memoryCostKiB: ARGON2_MEMORY_COST_KIB,
      parallelism: ARGON2_PARALLELISM,
      keyLength: KEY_LENGTH,
      salt: salt.toString("base64"),
    },
    cipher: {
      name: "aes-256-gcm",
      iv: iv.toString("base64"),
      tag: tag.toString("base64"),
    },
    ciphertext: encrypted.toString("base64"),
  };
}

function decryptEnvelope(envelope: VaultEnvelope, passphrase: string): string {
  if (envelope.kdf.name !== "argon2id") {
    throw new Error(`Unsupported vault KDF: ${envelope.kdf.name}`);
  }

  const salt = Buffer.from(envelope.kdf.salt, "base64");
  const iv = Buffer.from(envelope.cipher.iv, "base64");
  const tag = Buffer.from(envelope.cipher.tag, "base64");
  const ciphertext = Buffer.from(envelope.ciphertext, "base64");
  const key = deriveArgon2idKey(passphrase, salt);

  const decipher = createDecipheriv(ALGO, key, iv);
  decipher.setAuthTag(tag);
  const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  return plaintext.toString("utf8");
}

function decryptString(envelope: VaultEnvelope, passphrase: string): string {
  if (envelope.version !== VAULT_VERSION) {
    throw new Error(
      `Unsupported vault version: ${envelope.version}. Supported version: ${VAULT_VERSION}.`,
    );
  }
  if (envelope.payloadVersion > VAULT_VERSION) {
    throw new Error(
      `Unsupported vault payload version: ${envelope.payloadVersion}. Current app supports up to ${VAULT_VERSION}.`,
    );
  }
  return decryptEnvelope(envelope, passphrase);
}

async function walkFiles(dir: string): Promise<string[]> {
  if (!existsSync(dir)) return [];
  const out: string[] = [];
  const entries = await readdir(dir, { withFileTypes: true });

  for (const entry of entries) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      const nested = await walkFiles(full);
      out.push(...nested);
      continue;
    }
    if (entry.isFile()) out.push(full);
  }

  return out;
}

async function readMemorySnapshot(): Promise<Record<string, string>> {
  const files = await walkFiles(MEMORY_ROOT);
  const snapshot: Record<string, string> = {};

  for (const fullPath of files) {
    const rel = relative(MEMORY_ROOT, fullPath).replace(/\\/g, "/");
    snapshot[rel] = await readFile(fullPath, "utf8");
  }

  return snapshot;
}

async function readSoulSnapshot(): Promise<Record<string, string>> {
  const files = await walkFiles(SOUL_DIR);
  const snapshot: Record<string, string> = {};

  for (const fullPath of files) {
    const rel = relative(SOUL_DIR, fullPath).replace(/\\/g, "/");
    snapshot[rel] = await readFile(fullPath, "utf8");
  }

  return snapshot;
}

async function writeMemorySnapshot(memoryFiles: Record<string, string>): Promise<void> {
  await rm(MEMORY_ROOT, { recursive: true, force: true });
  await mkdir(MEMORY_ROOT, { recursive: true });

  for (const [rel, content] of Object.entries(memoryFiles)) {
    const safeRel = rel.replace(/^(\.\.(\/|\\|$))+/, "");
    const target = join(MEMORY_ROOT, safeRel);
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, content, "utf8");
  }
}

async function writeSoulSnapshot(soulFiles: Record<string, string>): Promise<void> {
  await rm(SOUL_DIR, { recursive: true, force: true });
  await mkdir(SOUL_DIR, { recursive: true });

  for (const [rel, content] of Object.entries(soulFiles)) {
    const safeRel = rel.replace(/^(\.\.(\/|\\|$))+/, "");
    const target = join(SOUL_DIR, safeRel);
    await mkdir(dirname(target), { recursive: true });
    await writeFile(target, content, "utf8");
  }
}

async function exportDatabaseSnapshot(): Promise<DatabaseSnapshot> {
  return {
    users: await db.select().from(users),
    messages: await db.select().from(messages),
    agentConfig: await db.select().from(agentConfig),
    telegramLinks: await db.select().from(telegramLinks),
    discordLinks: await db.select().from(discordLinks),
    tasks: await db.select().from(tasks),
    agentThreads: await db.select().from(agentThreads),
    langgraphCheckpoints: await db.select().from(langgraphCheckpoints),
    langgraphWrites: await db.select().from(langgraphWrites),
  };
}

async function restoreDatabaseSnapshot(snapshot: DatabaseSnapshot): Promise<void> {
  await db.delete(langgraphWrites);
  await db.delete(langgraphCheckpoints);
  await db.delete(agentThreads);
  await db.delete(tasks);
  await db.delete(discordLinks);
  await db.delete(telegramLinks);
  await db.delete(agentConfig);
  await db.delete(messages);
  await db.delete(users);

  if (snapshot.users.length) await db.insert(users).values(snapshot.users);
  if (snapshot.messages.length) await db.insert(messages).values(snapshot.messages);
  if (snapshot.agentConfig.length) await db.insert(agentConfig).values(snapshot.agentConfig);
  if (snapshot.telegramLinks.length) await db.insert(telegramLinks).values(snapshot.telegramLinks);
  if (snapshot.discordLinks.length) await db.insert(discordLinks).values(snapshot.discordLinks);
  if (snapshot.tasks.length) await db.insert(tasks).values(snapshot.tasks);
  if (snapshot.agentThreads.length) await db.insert(agentThreads).values(snapshot.agentThreads);
  if (snapshot.langgraphCheckpoints.length) {
    await db.insert(langgraphCheckpoints).values(snapshot.langgraphCheckpoints);
  }
  if (snapshot.langgraphWrites.length) {
    await db.insert(langgraphWrites).values(snapshot.langgraphWrites);
  }
}

export async function exportVault(passphrase: string): Promise<{
  filename: string;
  vault: string;
  size: number;
}> {
  const payload: VaultPayload = {
    version: VAULT_VERSION,
    createdAt: new Date().toISOString(),
    database: await exportDatabaseSnapshot(),
    memoryFiles: await readMemorySnapshot(),
    soulFiles: await readSoulSnapshot(),
  };

  const plaintext = JSON.stringify(payload);
  const envelope = encryptString(plaintext, passphrase);
  const vault = JSON.stringify(envelope);
  const dateStamp = new Date().toISOString().slice(0, 10);

  return {
    filename: `anima-vault-${dateStamp}.vault.json`,
    vault,
    size: Buffer.byteLength(vault, "utf8"),
  };
}

export async function importVault(vault: string, passphrase: string): Promise<{
  restoredUsers: number;
  restoredMemoryFiles: number;
}> {
  const envelope = JSON.parse(vault) as VaultEnvelope;
  const plaintext = decryptString(envelope, passphrase);
  const payload = JSON.parse(plaintext) as VaultPayload;

  if (payload.version > VAULT_VERSION) {
    throw new Error(
      `Unsupported vault payload version: ${payload.version}. Current app supports up to ${VAULT_VERSION}.`,
    );
  }

  await restoreDatabaseSnapshot(payload.database);
  await writeMemorySnapshot(payload.memoryFiles || {});
  await writeSoulSnapshot(payload.soulFiles || {});

  return {
    restoredUsers: payload.database.users.length,
    restoredMemoryFiles: Object.keys(payload.memoryFiles || {}).length,
  };
}
