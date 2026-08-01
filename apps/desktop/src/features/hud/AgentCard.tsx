import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { cn, liftDownRight } from "@anima/standard-templates";
import type { AgentStateData } from "@anima/api-client";
import { useAuth } from "../../context/AuthContext";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { api } from "../../lib/api";
import { CLIP_PATH } from "./hud.styles";

const POSITIVE_MOODS = new Set([
  "happy", "excited", "hopeful", "grateful",
  "content", "playful", "affectionate", "calm",
]);
const NEGATIVE_MOODS = new Set([
  "sad", "lonely", "tired", "angry",
  "frustrated", "anxious", "worried",
]);

function moodColor(emotion: string) {
  const e = emotion.toLowerCase().trim();
  if (POSITIVE_MOODS.has(e)) return "text-accent border-accent/40";
  if (NEGATIVE_MOODS.has(e)) return "text-destructive border-destructive/40";
  return "text-foreground/50 border-hairline-strong";
}

export function AgentCard() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const { agentName, avatarUrl } = useAgentProfile(user?.id);
  const [agentState, setAgentState] = useState<AgentStateData | null>(null);
  const [collapsed] = useState(() => {
    try { return localStorage.getItem("anima_nav_collapsed") === "true"; }
    catch { return false; }
  });

  useEffect(() => {
    if (user?.id == null) { setAgentState(null); return; }
    let active = true;
    api.consciousness
      .getAgentState(user.id)
      .then((data) => { if (active) setAgentState(data); })
      .catch(() => { if (active) setAgentState(null); });
    return () => { active = false; };
  }, [user?.id]);

  return (
    <div className={cn(liftDownRight, "group/leftcard self-start pointer-events-auto")}>
      {/* Border layer */}
      <div
        style={{ clipPath: CLIP_PATH.cutBottomRight }}
        className="p-px bg-foreground/[0.08] group-hover/leftcard:bg-[var(--color-accent-dark)] transition-colors duration-150"
      >
        {/* Fill layer */}
        <button
          onClick={() => navigate("/agent")}
          style={{ clipPath: CLIP_PATH.cutBottomRight }}
          className="relative flex items-center h-hud gap-0 bg-background/20 backdrop-blur-glass group-hover/leftcard:bg-accent transition-colors duration-150"
        >
          <span className="relative h-full aspect-square shrink-0 overflow-hidden">
            <img src={avatarUrl} alt={agentName} className="h-full w-full object-cover" />
            <span className="absolute inset-0 bg-accent/0 group-hover/leftcard:bg-black/10 transition-colors" />
          </span>

          <span className="w-px h-5 bg-foreground/[0.10] shrink-0" />

          {!collapsed && (
            <span className="grid gap-1 min-w-0 px-3">
              <span className="font-mono text-base font-semibold tracking-caps-3 text-foreground group-hover/leftcard:text-accent-foreground uppercase leading-none truncate max-w-[130px] transition-colors duration-200">
                {agentName}
              </span>
              {agentState?.dominantEmotion && (
                <span
                  className={cn(
                    "font-mono text-[7.5px] uppercase tracking-caps-3 leading-none border px-1.5 py-[3px] w-fit",
                    "transition-colors duration-200",
                    "group-hover/leftcard:bg-foreground group-hover/leftcard:border-foreground group-hover/leftcard:text-accent",
                    moodColor(agentState.dominantEmotion),
                  )}
                >
                  {agentState.dominantEmotion}
                </span>
              )}
            </span>
          )}
        </button>
      </div>
    </div>
  );
}
