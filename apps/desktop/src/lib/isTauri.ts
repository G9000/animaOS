/**
 * True only when the Tauri host has injected its internals into the page.
 *
 * The desktop UI also runs as a plain web app (`bun run dev:web`), where no
 * Tauri IPC exists. Native `invoke` calls must be guarded by this check so they
 * fail fast instead of rejecting on every poll.
 */
export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}
