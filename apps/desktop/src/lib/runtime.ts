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
 * Retrieve the sidecar nonce via Tauri IPC.
 *
 * The nonce is delivered through a trusted channel (Tauri command) rather
 * than over HTTP, so other local processes cannot obtain it.  Returns
 * an empty string in dev mode or when Tauri APIs are unavailable.
 */
export async function discoverSidecarNonce(): Promise<string | null> {
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const nonce = await invoke<string>("get_sidecar_nonce");
    return nonce || null;
  } catch {
    // Tauri IPC unavailable (browser dev mode) — no nonce.
    return null;
  }
}
