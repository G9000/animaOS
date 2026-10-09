import { api } from "./api";

export const PORTABLE_PREFERENCES_CHANGED_EVENT = "anima-portable-preferences-changed";
export const DEVICE_BACKGROUND_MEDIA_KEY = "anima-background-media-device";
export const DEVICE_BGM_TRACKS_KEY = "anima_bgm_device_tracks";

type PortablePreferenceKey = "theme" | "background" | "translateLanguage" | "ascii" | "clockFormat" | "dashboardNodePositions" | "dashboardClosedNodes" | "bgm";
type PortableValues = Partial<Record<PortablePreferenceKey, unknown>>;
type PreferenceApi = Pick<typeof api.preferences, "get" | "update">;

let activeUserId: number | null = null;
let generation = 0;
let values: PortableValues = {};
let writeQueue = Promise.resolve();

function dispatchChanged(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new window.Event(PORTABLE_PREFERENCES_CHANGED_EVENT));
  else globalThis.dispatchEvent?.(new Event(PORTABLE_PREFERENCES_CHANGED_EVENT));
}

export async function hydratePortablePreferences(userId: number, preferenceApi: PreferenceApi = api.preferences): Promise<void> {
  const current = ++generation;
  activeUserId = userId;
  values = {};
  const response = await preferenceApi.get(userId);
  if (current !== generation || activeUserId !== userId) return;
  values = response.values as PortableValues;
  dispatchChanged();
}

export function clearPortablePreferences(): void {
  generation += 1;
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
  const current = generation;
  if (userId === null) return;
  writeQueue = writeQueue.catch(() => {}).then(async () => {
    if (current !== generation || activeUserId !== userId) return;
    const response = await api.preferences.update(userId, { [key]: value });
    if (current !== generation || activeUserId !== userId) return;
    values = response.values as PortableValues;
    dispatchChanged();
  });
}
