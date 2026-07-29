import { useCallback, useEffect, useRef, useState } from "react";
import type { PendingInitiative } from "@anima/api-client";

import { api } from "../lib/api";
import {
  createGatedInitiativeFetch,
  createInitiativePoller,
  type InitiativePoller,
} from "../lib/initiativePoller";

/**
 * Global poll for IL3 pending initiatives: 60s interval plus a poll on
 * window focus. Poll failures are silent (locked session, server down);
 * `ack` is the user's dismiss/reply action.
 */
export function usePendingInitiatives(userId: number | null | undefined): {
  pending: PendingInitiative[];
  ack: (id: number) => Promise<void>;
} {
  const [pending, setPending] = useState<PendingInitiative[]>([]);
  const pollerRef = useRef<InitiativePoller | null>(null);

  useEffect(() => {
    if (userId == null) return;
    const poller = createInitiativePoller({
      // Every cycle re-checks the CURRENT presence config before fetching,
      // so withdrawing consent (initiative toggle or the master switch)
      // stops delivery within one cycle — the server's list endpoint does
      // not consult the config itself.
      fetchInitiatives: createGatedInitiativeFetch({
        getPresenceGate: async () => {
          const config = await api.presence.get(userId);
          return {
            enabled: config.enabled,
            initiativeEnabled: config.initiativeEnabled,
            quietHoursStart: config.quietHoursStart,
            quietHoursEnd: config.quietHoursEnd,
          };
        },
        fetchInitiatives: async () =>
          (await api.presence.initiatives(userId)).initiatives,
      }),
      ackInitiative: (id) => api.presence.ackInitiative(userId, id),
      onChange: setPending,
    });
    pollerRef.current = poller;
    poller.start();
    const onFocus = () => {
      void poller.pollNow();
    };
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      poller.stop();
      pollerRef.current = null;
      setPending([]);
    };
  }, [userId]);

  const ack = useCallback(async (id: number) => {
    await pollerRef.current?.ack(id);
  }, []);

  return { pending, ack };
}
