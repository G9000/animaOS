import { useCallback, useEffect, useState } from "react";
import type { DashboardNode } from "./nodes/node-types";
import {
  getPortablePreference,
  PORTABLE_PREFERENCES_CHANGED_EVENT,
  setPortablePreference,
} from "../../lib/portablePreferences";

type SavedNode = { x: number; y: number; width?: number; height?: number };

export function useNodePositions(nodes: DashboardNode[] | null) {
  const [hydratedNodes, setHydratedNodes] = useState<DashboardNode[] | null>(
    null,
  );
  const [preferenceRevision, setPreferenceRevision] = useState(0);

  useEffect(() => {
    const refresh = () => setPreferenceRevision((current) => current + 1);
    globalThis.addEventListener(PORTABLE_PREFERENCES_CHANGED_EVENT, refresh);
    return () =>
      globalThis.removeEventListener(PORTABLE_PREFERENCES_CHANGED_EVENT, refresh);
  }, []);

  useEffect(() => {
    if (!nodes) return;

    const saved = getPortablePreference<Record<string, SavedNode>>(
      "dashboardNodePositions",
      {},
    );

    const next = nodes.map((node) => {
      const s = saved[node.id];
      if (!s) return node;
      return {
        ...node,
        position: { x: s.x, y: s.y },
        ...(s.width != null ? { width: s.width } : {}),
        ...(s.height != null ? { height: s.height } : {}),
      };
    });

    setHydratedNodes(next);
  }, [nodes, preferenceRevision]);

  const persistPositions = useCallback(
    (updated: DashboardNode[]) => {
      const positions: Record<string, SavedNode> = {};
      for (const node of updated) {
        positions[node.id] = {
          x: node.position.x,
          y: node.position.y,
          ...(node.width != null ? { width: node.width } : {}),
          ...(node.height != null ? { height: node.height } : {}),
        };
      }
      setPortablePreference("dashboardNodePositions", positions);
      setHydratedNodes(updated);
    },
    [],
  );

  return { hydratedNodes, persistPositions };
}
