import { invoke } from "@tauri-apps/api/core";
import type { DiaryDraftCompletionToken } from "@anima/api-client";

const KEY_DOMAIN = "anima-draft-storage-key-v1\0";
const TOKEN_DOMAIN = "anima-draft-completion-token-v1\0";
const AUDIENCE_DOMAIN = "anima-draft-cleanup-audience-v1\0";

export interface IssuedDraftCleanupCapability {
  capability: string;
  expiresInMs: number;
}

export interface DraftCleanupAuthority {
  issue(audienceDigest: string): Promise<IssuedDraftCleanupCapability>;
  consume(capability: string, audienceDigest: string): Promise<boolean>;
}

type NativeInvoke = <T>(command: string, args: Record<string, unknown>) => Promise<T>;

function bytes(value: string): Uint8Array {
  return new TextEncoder().encode(value.normalize("NFC"));
}

function u32be(value: number): Uint8Array {
  if (!Number.isSafeInteger(value) || value < 0 || value > 0xffff_ffff) {
    throw new Error("draft cleanup field is too large");
  }
  const result = new Uint8Array(4);
  new DataView(result.buffer).setUint32(0, value, false);
  return result;
}

function u64be(value: number): Uint8Array {
  if (!Number.isSafeInteger(value) || value < 1) {
    throw new Error("draft cleanup revision is invalid");
  }
  const result = new Uint8Array(8);
  new DataView(result.buffer).setBigUint64(0, BigInt(value), false);
  return result;
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const result = new Uint8Array(parts.reduce((total, part) => total + part.length, 0));
  let offset = 0;
  for (const part of parts) {
    result.set(part, offset);
    offset += part.length;
  }
  return result;
}

function decodeLowerSha256(value: string): Uint8Array {
  if (!/^[0-9a-f]{64}$/.test(value)) throw new Error("draft completion hash is malformed");
  return Uint8Array.from({ length: 32 }, (_, index) => Number.parseInt(value.slice(index * 2, index * 2 + 2), 16));
}

async function digest(value: Uint8Array): Promise<Uint8Array> {
  const copy = new Uint8Array(value.byteLength);
  copy.set(value);
  return new Uint8Array(await crypto.subtle.digest("SHA-256", copy.buffer));
}

function hex(value: Uint8Array): string {
  return [...value].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function deriveDraftCleanupAudience(
  storageKey: string,
  token: DiaryDraftCompletionToken,
): Promise<string> {
  const key = bytes(storageKey);
  const draftId = bytes(token.draftId);
  const keyDigest = await digest(concat(bytes(KEY_DOMAIN), u32be(key.length), key));
  const tokenDigest = await digest(concat(
    bytes(TOKEN_DOMAIN),
    u32be(draftId.length),
    draftId,
    u64be(token.clientRevision),
    decodeLowerSha256(token.contentSha256),
  ));
  return hex(await digest(concat(bytes(AUDIENCE_DOMAIN), keyDigest, tokenDigest)));
}

export function draftCleanupAuthorityFromInvoke(nativeInvoke: NativeInvoke): DraftCleanupAuthority {
  return {
    issue: (audienceDigest) => nativeInvoke<IssuedDraftCleanupCapability>(
      "draft_cleanup_issue_v1",
      { audienceDigest },
    ),
    consume: (capability, audienceDigest) => nativeInvoke<boolean>(
      "draft_cleanup_consume_v1",
      { capability, audienceDigest },
    ),
  };
}

export function packagedDraftCleanupAuthority(): DraftCleanupAuthority | undefined {
  if (typeof window === "undefined" || !("__TAURI_INTERNALS__" in window)) return undefined;
  return draftCleanupAuthorityFromInvoke(invoke);
}
