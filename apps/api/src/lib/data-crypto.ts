import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { getActiveDek } from "./unlock-session";

const ALGO = "aes-256-gcm";
const IV_LENGTH = 12;
const PREFIX = "enc1";

export function encryptTextWithDek(plaintext: string, dek: Buffer): string {
  const iv = randomBytes(IV_LENGTH);
  const cipher = createCipheriv(ALGO, dek, iv);
  const ciphertext = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return `${PREFIX}:${iv.toString("base64")}:${tag.toString("base64")}:${ciphertext.toString("base64")}`;
}

export function decryptTextWithDek(serialized: string, dek: Buffer): string {
  if (!serialized.startsWith(`${PREFIX}:`)) return serialized;
  const parts = serialized.split(":");
  if (parts.length !== 4) throw new Error("Invalid encrypted payload format.");

  const [, ivB64, tagB64, ctB64] = parts;
  const iv = Buffer.from(ivB64, "base64");
  const tag = Buffer.from(tagB64, "base64");
  const ciphertext = Buffer.from(ctB64, "base64");

  const decipher = createDecipheriv(ALGO, dek, iv);
  decipher.setAuthTag(tag);
  const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
  return plaintext.toString("utf8");
}

export function maybeEncryptForUser(userId: number, plaintext: string): string {
  const dek = getActiveDek(userId);
  if (!dek) return plaintext;
  return encryptTextWithDek(plaintext, dek);
}

export function maybeDecryptForUser(userId: number, value: string): string {
  const dek = getActiveDek(userId);
  if (!dek) return value;
  return decryptTextWithDek(value, dek);
}

export function requireDekForUser(userId: number): Buffer {
  const dek = getActiveDek(userId);
  if (!dek) {
    throw new Error("Session key is locked. Please sign in again.");
  }
  return dek;
}
