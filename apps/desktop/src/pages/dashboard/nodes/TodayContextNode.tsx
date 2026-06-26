import type { NodeProps } from "@xyflow/react";
import { TodayContextPanel } from "../../../components/TodayContextPanel";
import type { TodayContextNode } from "./node-types";
import type { TodayContextDraft } from "../../../lib/today-context";
import { NodeShell } from "./NodeShell";

export function TodayContextNode({ data }: NodeProps<TodayContextNode>) {
  const { context, greeting, onSave, onClear, onClose } = data;

  return (
    <NodeShell
      onClose={onClose}
      hideHeader
      className="w-80 border-0 shadow-none"
    >
      <TodayContextPanel
        context={context}
        greeting={context ? null : greeting ?? "How are you arriving today?"}
        onSave={(draft: TodayContextDraft) =>
          onSave({
            date: context?.date ?? new Date().toISOString().slice(0, 10),
            mood: draft.mood || null,
            energy: draft.energy || null,
            note: draft.note || null,
          })
        }
        onClear={onClear}
      />
    </NodeShell>
  );
}
