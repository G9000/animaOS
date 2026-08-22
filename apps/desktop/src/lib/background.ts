import { appDataDir, join } from "@tauri-apps/api/path";
import { convertFileSrc } from "@tauri-apps/api/core";
import { mkdir, open, BaseDirectory } from "@tauri-apps/plugin-fs";
import { dispatchBackgroundChanged } from "./events";
import {
  DEVICE_BACKGROUND_MEDIA_KEY,
  getPortablePreference,
  setPortablePreference,
} from "./portablePreferences";

export type BackgroundType = "default" | "color" | "gradient" | "image" | "video";

export interface BackgroundConfig {
  type: BackgroundType;
  /**
   * - For `color`: a CSS color value.
   * - For `gradient`: a CSS background-image gradient value.
   * - For `image`/`video`: either a data URL (web dev fallback) or a file name
   *   stored in the Tauri app-data `backgrounds` directory.
   */
  value?: string;
  fit?: "cover" | "contain" | "repeat";
  dim?: number;
  blur?: number;
}

export const DEFAULT_BACKGROUND: BackgroundConfig = {
  type: "default",
  fit: "cover",
  dim: 0,
  blur: 0,
};

function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function getBackgroundConfig(): BackgroundConfig {
  try {
    const raw = localStorage.getItem(DEVICE_BACKGROUND_MEDIA_KEY);
    if (raw) return normalizeBackground(JSON.parse(raw) as BackgroundConfig);
  } catch {}
  const portable = getPortablePreference<BackgroundConfig | null>("background", null);
  return portable ? normalizeBackground(portable) : DEFAULT_BACKGROUND;
}

function normalizeBackground(config: BackgroundConfig): BackgroundConfig {
  return {
    ...DEFAULT_BACKGROUND,
    ...config,
    dim: Math.max(0, Math.min(1, config.dim ?? 0)),
    blur: Math.max(0, Math.min(64, config.blur ?? 0)),
  };
}

export function saveBackgroundConfig(config: BackgroundConfig): void {
  const normalized = normalizeBackground(config);
  const portableMedia =
    (normalized.type === "image" || normalized.type === "video") &&
    normalized.value?.startsWith("corefs://object/");
  if (
    normalized.type === "default" ||
    normalized.type === "color" ||
    normalized.type === "gradient" ||
    portableMedia
  ) {
    setPortablePreference("background", normalized);
    try {
      localStorage.removeItem(DEVICE_BACKGROUND_MEDIA_KEY);
    } catch {}
  } else {
    try {
      localStorage.setItem(DEVICE_BACKGROUND_MEDIA_KEY, JSON.stringify(normalized));
    } catch {
      // Device-local host media may be unavailable in browser privacy modes.
    }
  }
  dispatchBackgroundChanged();
}

/**
 * Resolve the configured background to a URL that can be used in `<img>` or
 * `<video>` elements. For color/gradient types the raw CSS value is returned.
 * For media types, Tauri file paths are converted to asset URLs.
 */
export async function resolveBackgroundUrl(
  config: BackgroundConfig,
): Promise<string | null> {
  if (config.type === "default" || !config.value) return null;
  if (config.type === "color" || config.type === "gradient") return config.value;

  if (
    config.value.startsWith("data:") ||
    config.value.startsWith("blob:") ||
    config.value.startsWith("http")
  ) {
    return config.value;
  }

  if (isTauri()) {
    try {
      const dir = await appDataDir();
      const path = await join(dir, "backgrounds", config.value);
      return convertFileSrc(path);
    } catch (err) {
      console.error("Failed to resolve background URL:", err);
      return null;
    }
  }

  return null;
}

function sanitizeFileName(name: string): string {
  const safe = name
    .replace(/[/\\]/g, "_")
    .replace(/\.\./g, "_")
    .replace(/[^a-zA-Z0-9._-]/g, "_");
  return safe || "background.bin";
}

/**
 * Persist an uploaded media file. In Tauri this writes to the app-data
 * directory and returns the stored file name. In a plain browser it returns a
 * data URL (subject to localStorage size limits).
 */
export async function saveBackgroundFile(file: File): Promise<string> {
  if (isTauri()) {
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      const name = sanitizeFileName(file.name);

      await mkdir("backgrounds", {
        baseDir: BaseDirectory.AppData,
        recursive: true,
      });

      const handle = await open(`backgrounds/${name}`, {
        write: true,
        create: true,
        truncate: true,
        baseDir: BaseDirectory.AppData,
      });

      try {
        await handle.write(bytes);
      } finally {
        await handle.close();
      }

      return name;
    } catch (err) {
      console.error("Failed to save background file to disk:", err);
      throw err;
    }
  }

  throw new Error(
    "Background media persistence requires the desktop host; browser data URLs are not stored.",
  );
}

/**
 * Infer a background type from a file's MIME type.
 */
export function inferBackgroundType(mime: string): BackgroundType | null {
  if (mime.startsWith("image/gif")) return "image";
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  return null;
}
