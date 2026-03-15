const DEFAULT_API_BASE = "http://127.0.0.1:3031/api";

const configuredBase = import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE;

export const API_BASE = configuredBase.replace(/\/$/, "");

/** Base origin without the /api suffix, used for the health endpoint. */
export const API_ORIGIN = API_BASE.replace(/\/api$/, "");

export function apiUrl(path: string): string {
  if (!path.startsWith("/")) return `${API_BASE}/${path}`;
  return `${API_BASE}${path}`;
}

/**
 * Discover the sidecar nonce from the /health endpoint.
 * Returns the nonce string if the server provided one, otherwise null.
 */
export async function discoverSidecarNonce(): Promise<string | null> {
  try {
    const resp = await fetch(`${API_ORIGIN}/health`);
    if (!resp.ok) return null;
    const data = (await resp.json()) as { nonce?: string };
    return data.nonce ?? null;
  } catch {
    return null;
  }
}
