import { api } from "./api";

export const PORTABLE_PREFERENCES_CHANGED_EVENT =
  "anima-portable-preferences-changed";

const THEME_KEY = "anima-theme";
const BACKGROUND_KEY = "anima-background-config";
const TRANSLATE_LANGUAGE_KEY = "anima-translate-lang";
const ASCII_KEY = "anima_ascii_settings";
const CLOCK_KEY = "anima_clock_format";
const DASHBOARD_POSITIONS_KEY = "anima_dashboard_node_positions";
const DASHBOARD_CLOSED_KEY = "anima_dashboard_closed_nodes";
const BGM_MUTED_KEY = "anima_bgm_muted";
const BGM_STATE_KEY = "anima_bgm_state";
export const DEVICE_BACKGROUND_MEDIA_KEY = "anima-background-media-device";
export const DEVICE_BGM_TRACKS_KEY = "anima_bgm_device_tracks";

type PortablePreferenceKey =
  | "theme"
  | "background"
  | "translateLanguage"
  | "ascii"
  | "clockFormat"
  | "dashboardNodePositions"
  | "dashboardClosedNodes"
  | "bgm";

type PortableValues = Partial<Record<PortablePreferenceKey, unknown>>;
type PreferenceApi = Pick<typeof api.preferences, "get" | "update">;

interface LegacyValue {
  key: string;
  raw: string;
}

let activeUserId: number | null = null;
let values: PortableValues = {};
let writeQueue = Promise.resolve();

function dispatchChanged(): void {
  globalThis.dispatchEvent(new Event(PORTABLE_PREFERENCES_CHANGED_EVENT));
}

function jsonValue(raw: string): unknown {
  return JSON.parse(raw) as unknown;
}

function canonicalJson(value: unknown): string {
  if (value === undefined) return "null";
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, item]) => item !== undefined)
      .sort(([left], [right]) => left.localeCompare(right));
    return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

function writeDeviceValue(storage: Storage, key: string, raw: string): void {
  storage.setItem(key, raw);
  if (storage.getItem(key) !== raw) {
    throw new Error(`Device preference handoff failed for ${key}.`);
  }
}

function isPortableBackground(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const candidate = value as { type?: unknown; value?: unknown };
  if (candidate.type === "default") return true;
  if (candidate.type === "color" || candidate.type === "gradient") {
    return typeof candidate.value === "string";
  }
  return (
    (candidate.type === "image" || candidate.type === "video") &&
    typeof candidate.value === "string" &&
    candidate.value.startsWith("corefs://object/")
  );
}

function legacySnapshot(storage: Storage): LegacyValue[] {
  return [
    THEME_KEY,
    BACKGROUND_KEY,
    TRANSLATE_LANGUAGE_KEY,
    ASCII_KEY,
    CLOCK_KEY,
    DASHBOARD_POSITIONS_KEY,
    DASHBOARD_CLOSED_KEY,
    BGM_MUTED_KEY,
    BGM_STATE_KEY,
  ].flatMap((key) => {
    const raw = storage.getItem(key);
    return raw === null ? [] : [{ key, raw }];
  });
}

function decodeLegacy(snapshot: LegacyValue[], storage: Storage): PortableValues {
  const raw = new Map(snapshot.map((item) => [item.key, item.raw]));
  const patch: PortableValues = {};
  const theme = raw.get(THEME_KEY);
  if (theme === "dark" || theme === "light" || theme === "system") {
    patch.theme = theme;
  }
  const language = raw.get(TRANSLATE_LANGUAGE_KEY);
  if (language) patch.translateLanguage = language;
  const clock = raw.get(CLOCK_KEY);
  if (clock === "12h" || clock === "24h") patch.clockFormat = clock;

  for (const [storageKey, preferenceKey] of [
    [ASCII_KEY, "ascii"],
    [DASHBOARD_POSITIONS_KEY, "dashboardNodePositions"],
    [DASHBOARD_CLOSED_KEY, "dashboardClosedNodes"],
  ] as const) {
    const encoded = raw.get(storageKey);
    if (!encoded) continue;
    try {
      patch[preferenceKey] = jsonValue(encoded);
    } catch {
      // Invalid legacy preferences are omitted and scrubbed after verification.
    }
  }

  const backgroundRaw = raw.get(BACKGROUND_KEY);
  if (backgroundRaw) {
    let background: unknown;
    try {
      background = jsonValue(backgroundRaw);
    } catch {
      // Invalid legacy background data is not promoted into CoreFS.
    }
    if (background !== undefined) {
      if (isPortableBackground(background)) patch.background = background;
      else writeDeviceValue(storage, DEVICE_BACKGROUND_MEDIA_KEY, backgroundRaw);
    }
  }

  let muted = raw.get(BGM_MUTED_KEY) === "true";
  const bgmRaw = raw.get(BGM_STATE_KEY);
  let currentId: string | undefined;
  if (bgmRaw) {
    try {
      const bgm = jsonValue(bgmRaw) as {
        currentId?: unknown;
        muted?: unknown;
        userTracks?: unknown;
      };
      if (typeof bgm.muted === "boolean") muted = bgm.muted;
      if (typeof bgm.currentId === "string" && bgm.currentId.startsWith("builtin-")) {
        currentId = bgm.currentId;
      }
      if (Array.isArray(bgm.userTracks) && bgm.userTracks.length > 0) {
        const deviceTracks = JSON.stringify({
          currentId:
            typeof bgm.currentId === "string" && bgm.currentId.startsWith("user-")
              ? bgm.currentId
              : null,
          userTracks: bgm.userTracks,
        });
        writeDeviceValue(storage, DEVICE_BGM_TRACKS_KEY, deviceTracks);
      }
    } catch (error) {
      if (error instanceof Error && error.message.includes("handoff failed")) throw error;
      // Invalid legacy BGM data is not promoted into CoreFS.
    }
  }
  if (raw.has(BGM_MUTED_KEY) || bgmRaw) {
    patch.bgm = { currentId, muted };
  }
  return patch;
}

function removeExactSnapshot(storage: Storage, snapshot: LegacyValue[]): boolean {
  for (const item of snapshot) {
    if (storage.getItem(item.key) !== item.raw) return false;
    storage.removeItem(item.key);
  }
  return snapshot.every((item) => storage.getItem(item.key) === null);
}

export async function hydratePortablePreferences(
  userId: number,
  preferenceApi: PreferenceApi = api.preferences,
  storage: Storage = localStorage,
): Promise<void> {
  activeUserId = userId;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const snapshot = legacySnapshot(storage);
    if (snapshot.length === 0) {
      const response = await preferenceApi.get(userId);
      values = response.values as PortableValues;
      dispatchChanged();
      return;
    }
    const patch = decodeLegacy(snapshot, storage);
    const response =
      Object.keys(patch).length > 0
        ? await preferenceApi.update(userId, patch)
        : await preferenceApi.get(userId);
    for (const [key, value] of Object.entries(patch)) {
      if (canonicalJson(response.values[key]) !== canonicalJson(value)) {
        throw new Error(`Encrypted preference verification failed for ${key}.`);
      }
    }
    values = response.values as PortableValues;
    dispatchChanged();
    if (removeExactSnapshot(storage, snapshot)) return;
  }
  throw new Error("Portable preferences changed during migration; retry after unlock.");
}

export function clearPortablePreferences(): void {
  activeUserId = null;
  values = {};
  writeQueue = Promise.resolve();
  dispatchChanged();
}

export function getPortablePreference<T>(key: PortablePreferenceKey, fallback: T): T {
  return (values[key] as T | undefined) ?? fallback;
}

export function setPortablePreference<T>(key: PortablePreferenceKey, value: T): void {
  values = { ...values, [key]: value };
  dispatchChanged();
  const userId = activeUserId;
  if (userId === null) return;
  writeQueue = writeQueue
    .catch(() => {})
    .then(async () => {
      const response = await api.preferences.update(userId, { [key]: value });
      if (activeUserId !== userId) return;
      values = response.values as PortableValues;
      dispatchChanged();
    });
}
