import type { NodeProps } from "@xyflow/react";
import { cn } from "@anima/standard-templates";
import type { MoodNode } from "./node-types";
import { getMoodEmoji } from "../mood-helpers";
import { NodeShell } from "./NodeShell";

function getMoodTone(emotion: string | null): string {
  if (!emotion) return "text-muted-foreground/40";
  const e = emotion.toLowerCase().trim();
  if (["happy", "excited", "playful", "hopeful", "grateful"].includes(e)) {
    return "text-primary/70";
  }
  if (["sad", "lonely", "tired"].includes(e)) {
    return "text-muted-foreground/50";
  }
  if (["angry", "frustrated", "anxious", "worried"].includes(e)) {
    return "text-destructive/60";
  }
  return "text-muted-foreground/50";
}

export function MoodNode({ data }: NodeProps<MoodNode>) {
  const { mood, agentName, onClose } = data;
  const emotion = mood?.dominantEmotion ?? null;
  const emoji = getMoodEmoji(emotion);

  return (
    <NodeShell title="Moodboard" onClose={onClose} className="w-64">
      <div className="p-4">
        {emotion ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-2xl leading-none select-none">{emoji}</span>
              <span className="text-sm text-muted-foreground/70">
                {agentName} feels{" "}
                <span
                  className={cn("font-medium capitalize", getMoodTone(emotion))}
                >
                  {emotion}
                </span>
              </span>
            </div>
            {mood?.recentSignals[0]?.trajectory && (
              <span className="font-mono text-[9px] tracking-wider text-muted-foreground/25">
                · {mood.recentSignals[0].trajectory}
              </span>
            )}
            {mood?.synthesizedContext && (
              <p className="text-xs text-foreground/50 leading-relaxed line-clamp-3">
                {mood.synthesizedContext}
              </p>
            )}
          </div>
        ) : (
          <p className="font-mono text-[10px] text-muted-foreground/30 tracking-wider">
            NO MOOD DATA
          </p>
        )}
      </div>
    </NodeShell>
  );
}
