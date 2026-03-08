import type { Context } from "hono";
import { exportVault, importVault } from "../../lib/vault";

function requirePassphrase(input: unknown): string | null {
  if (
    !input ||
    typeof input !== "object" ||
    typeof (input as { passphrase?: unknown }).passphrase !== "string"
  ) {
    return null;
  }

  const passphrase = (input as { passphrase: string }).passphrase.trim();
  if (passphrase.length < 8) return null;
  return passphrase;
}

export async function exportEncryptedVault(c: Context) {
  const body = await c.req.json().catch(() => ({}));
  const passphrase = requirePassphrase(body);
  if (!passphrase) {
    return c.json({ error: "Passphrase is required (min 8 chars)." }, 400);
  }

  const result = await exportVault(passphrase);
  return c.json(result);
}

export async function importEncryptedVault(c: Context) {
  const body = await c.req.json().catch(() => ({}));
  const passphrase = requirePassphrase(body);
  if (!passphrase) {
    return c.json({ error: "Passphrase is required (min 8 chars)." }, 400);
  }

  const vault = (body as { vault?: unknown }).vault;
  if (typeof vault !== "string" || !vault.trim()) {
    return c.json({ error: "Vault payload is required." }, 400);
  }

  try {
    const result = await importVault(vault, passphrase);
    return c.json({ status: "ok", ...result });
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Failed to import vault.";
    return c.json({ error: message }, 400);
  }
}
