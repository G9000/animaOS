export const SETTINGS_CHANGED_EVENT = "anima-settings-changed";
export const AGENT_PROFILE_CHANGED_EVENT = "anima-agent-profile-changed";
export const BACKGROUND_CHANGED_EVENT = "anima-background-changed";

export function dispatchSettingsChanged(): void {
  window.dispatchEvent(new Event(SETTINGS_CHANGED_EVENT));
}

export function dispatchAgentProfileChanged(): void {
  window.dispatchEvent(new Event(AGENT_PROFILE_CHANGED_EVENT));
}

export function dispatchBackgroundChanged(): void {
  window.dispatchEvent(new Event(BACKGROUND_CHANGED_EVENT));
}
