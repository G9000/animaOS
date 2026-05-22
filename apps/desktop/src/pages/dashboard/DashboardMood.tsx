import type { EmotionalContextData } from "@anima/api-client";
import { cn } from "@anima/standard-templates";

interface DashboardMoodProps {
  mood: EmotionalContextData | null;
  agentName: string;
}

const MOOD_EMOJI: Record<string, string> = {
  calm: "🌿",
  happy: "✨",
  excited: "⚡",
  curious: "🔍",
  anxious: "🌊",
  sad: "🌧",
  angry: "🔥",
  frustrated: "💢",
  hopeful: "🌅",
  lonely: "🌑",
  content: "☕",
  tired: "🌫",
  playful: "🎈",
  worried: "🌩",
  grateful: "🙏",
  confused: "🌀",
  protective: "🛡",
  affectionate: "💗",
};

function getMoodEmoji(emotion: string | null): string {
  if (!emotion) return "◌";
  const normalized = emotion.toLowerCase().trim();
  return MOOD_EMOJI[normalized] || "◌";
}

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

export function DashboardMood({ mood, agentName }: DashboardMoodProps) {
  if (!mood || !mood.dominantEmotion) {
    return null;
  }

  const emotion = mood.dominantEmotion;
  const emoji = getMoodEmoji(emotion);

  return (
    <div className="animate-fade-in flex items-center gap-2">
      <span className="text-base leading-none select-none">{emoji}</span>
      <span className="text-body text-muted-foreground/50">
        {agentName} feels{" "}
        <span className={cn("font-medium capitalize", getMoodTone(emotion))}>
          {emotion}
        </span>
      </span>
      {mood.recentSignals[0]?.trajectory && (
        <span className="font-mono text-[9px] tracking-wider text-muted-foreground/25">
          · {mood.recentSignals[0].trajectory}
        </span>
      )}
    </div>
  );
}
