import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { argon2id } from "@noble/hashes/argon2.js";

const KDF_TIME_COST = 3;
const KDF_MEMORY_COST_KIB = 64 * 1024;
const KDF_PARALLELISM = 1;
const KEY_LENGTH = 32;
const ALGO = "aes-256-gcm";
const IV_LENGTH = 12;

export interface WrappedDekRecord {
  kdfSalt: string;
  kdfTimeCost: number;
  kdfMemoryCostKib: number;
  kdfParallelism: number;
  kdfKeyLength: number;
  wrapIv: string;
  wrapTag: string;
  wrappedDek: string;
}

function deriveKek(passphrase: string, salt: Uint8Array): Buffer {
  const passphraseBytes = new TextEncoder().encode(passphrase);
  const key = argon2id(passphraseBytes, salt, {
    t: KDF_TIME_COST,
    m: KDF_MEMORY_COST_KIB,
    p: KDF_PARALLELISM,
    dkLen: KEY_LENGTH,
  });
  return Buffer.from(key);
}

export function createWrappedDek(passphrase: string): {
  dek: Buffer;
  record: WrappedDekRecord;
} {
  const dek = randomBytes(KEY_LENGTH);
  const salt = randomBytes(16);
  const iv = randomBytes(IV_LENGTH);
  const kek = deriveKek(passphrase, salt);

  const cipher = createCipheriv(ALGO, kek, iv);
  const ciphertext = Buffer.concat([cipher.update(dek), cipher.final()]);
  const tag = cipher.getAuthTag();

  return {
    dek,
    record: {
      kdfSalt: salt.toString("base64"),
      kdfTimeCost: KDF_TIME_COST,
      kdfMemoryCostKib: KDF_MEMORY_COST_KIB,
      kdfParallelism: KDF_PARALLELISM,
      kdfKeyLength: KEY_LENGTH,
      wrapIv: iv.toString("base64"),
      wrapTag: tag.toString("base64"),
      wrappedDek: ciphertext.toString("base64"),
    },
  };
}

export function unwrapDek(passphrase: string, record: WrappedDekRecord): Buffer {
  const salt = Buffer.from(record.kdfSalt, "base64");
  const iv = Buffer.from(record.wrapIv, "base64");
  const tag = Buffer.from(record.wrapTag, "base64");
  const ciphertext = Buffer.from(record.wrappedDek, "base64");

  const passphraseBytes = new TextEncoder().encode(passphrase);
  const key = argon2id(passphraseBytes, salt, {
    t: record.kdfTimeCost,
    m: record.kdfMemoryCostKib,
    p: record.kdfParallelism,
    dkLen: record.kdfKeyLength,
  });

  const decipher = createDecipheriv(ALGO, Buffer.from(key), iv);
  decipher.setAuthTag(tag);
  const plaintext = Buffer.concat([decipher.update(ciphertext), decipher.final()]);

  if (plaintext.length !== KEY_LENGTH) {
    throw new Error("Invalid key material.");
  }

  return plaintext;
}
