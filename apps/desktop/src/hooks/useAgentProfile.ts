import { useCallback, useEffect, useRef, useState } from "react";
import personaAvatar from "../assets/persona-default.svg";
import { api, getUnlockToken } from "../lib/api";
import { AGENT_PROFILE_CHANGED_EVENT } from "../lib/events";
import { API_BASE } from "../lib/runtime";

export interface AgentProfileState {
  agentName: string;
  relationship: string;
  agentType: string;
  avatarUrl: string;
  hasCustomAvatar: boolean;
}

export interface UseAgentProfileResult extends AgentProfileState {
  refresh: () => Promise<void>;
}

interface LoadedAgentProfile extends AgentProfileState {
  objectUrl: string | null;
}

const DEFAULT_AGENT_PROFILE_STATE: AgentProfileState = {
  agentName: "ANIMA",
  relationship: "",
  agentType: "",
  avatarUrl: personaAvatar,
  hasCustomAvatar: false,
};

async function loadAgentAvatar(
  avatarPath: string | null | undefined,
): Promise<
  Pick<LoadedAgentProfile, "avatarUrl" | "hasCustomAvatar" | "objectUrl">
> {
  if (!avatarPath) {
    return {
      avatarUrl: personaAvatar,
      hasCustomAvatar: false,
      objectUrl: null,
    };
  }

  const token = getUnlockToken();
  const headers: HeadersInit = token ? { "x-anima-unlock": token } : {};
  const response = await fetch(`${API_BASE}${avatarPath}`, { headers });

  if (!response.ok) {
    return {
      avatarUrl: personaAvatar,
      hasCustomAvatar: false,
      objectUrl: null,
    };
  }

  const objectUrl = URL.createObjectURL(await response.blob());
  return {
    avatarUrl: objectUrl,
    hasCustomAvatar: true,
    objectUrl,
  };
}

async function loadAgentProfile(userId: number): Promise<LoadedAgentProfile> {
  const profile = await api.consciousness.getAgentProfile(userId);
  const avatar = await loadAgentAvatar(profile.avatarUrl);

  return {
    agentName:
      profile.agentName?.trim() || DEFAULT_AGENT_PROFILE_STATE.agentName,
    relationship: profile.relationship ?? "",
    agentType: profile.agentType ?? "",
    avatarUrl: avatar.avatarUrl,
    hasCustomAvatar: avatar.hasCustomAvatar,
    objectUrl: avatar.objectUrl,
  };
}

export function useAgentProfile(
  userId: number | null | undefined,
): UseAgentProfileResult {
  const [profile, setProfile] = useState(DEFAULT_AGENT_PROFILE_STATE);
  const avatarObjectUrlRef = useRef<string | null>(null);
  const requestIdRef = useRef(0);

  const clearAvatarObjectUrl = useCallback(() => {
    if (avatarObjectUrlRef.current) {
      URL.revokeObjectURL(avatarObjectUrlRef.current);
      avatarObjectUrlRef.current = null;
    }
  }, []);

  const replaceAvatarObjectUrl = useCallback((nextObjectUrl: string | null) => {
    const previousObjectUrl = avatarObjectUrlRef.current;
    avatarObjectUrlRef.current = nextObjectUrl;

    if (previousObjectUrl) {
      URL.revokeObjectURL(previousObjectUrl);
    }
  }, []);

  const refresh = useCallback(async () => {
    const requestId = ++requestIdRef.current;

    if (userId == null) {
      clearAvatarObjectUrl();
      setProfile(DEFAULT_AGENT_PROFILE_STATE);
      return;
    }

    try {
      const nextProfile = await loadAgentProfile(userId);

      if (requestId !== requestIdRef.current) {
        if (nextProfile.objectUrl) {
          URL.revokeObjectURL(nextProfile.objectUrl);
        }
        return;
      }

      replaceAvatarObjectUrl(nextProfile.objectUrl);
      setProfile({
        agentName: nextProfile.agentName,
        relationship: nextProfile.relationship,
        agentType: nextProfile.agentType,
        avatarUrl: nextProfile.avatarUrl,
        hasCustomAvatar: nextProfile.hasCustomAvatar,
      });
    } catch {
      if (requestId !== requestIdRef.current) {
        return;
      }
    }
  }, [clearAvatarObjectUrl, replaceAvatarObjectUrl, userId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const handleProfileChanged = () => {
      void refresh();
    };

    window.addEventListener(AGENT_PROFILE_CHANGED_EVENT, handleProfileChanged);
    return () => {
      window.removeEventListener(
        AGENT_PROFILE_CHANGED_EVENT,
        handleProfileChanged,
      );
    };
  }, [refresh]);

  useEffect(() => {
    return () => {
      requestIdRef.current += 1;
      clearAvatarObjectUrl();
    };
  }, [clearAvatarObjectUrl]);

  return {
    ...profile,
    refresh,
  };
}
