import { useEffect, useRef, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { ChevronRightIcon, ChevronLeftIcon, cn } from "@anima/standard-templates";
import type { AgentStateData } from "@anima/api-client";
import { useAuth } from "../../context/AuthContext";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { api } from "../../lib/api";
import { TOP_NAV_ITEMS } from "./nav-items";

const POSITIVE_MOODS = new Set(["happy", "excited", "hopeful", "grateful", "content", "playful", "affectionate", "calm"]);
const NEGATIVE_MOODS = new Set(["sad", "lonely", "tired", "angry", "frustrated", "anxious", "worried"]);

function moodBadgeClass(emotion: string): string {
  const e = emotion.toLowerCase().trim();
  if (POSITIVE_MOODS.has(e)) return "bg-accent/30 text-accent border border-accent/30";
  if (NEGATIVE_MOODS.has(e)) return "bg-destructive/20 text-destructive border border-destructive/20";
  return "bg-border text-foreground/70 border border-border";
}


type TopNavAgentButtonProps = {
  agentName: string;
  avatarUrl: string;
  dominantEmotion: string | null;
  stateThought?: string | null;
  expanded: boolean;
  onClick: () => void;
};

export function TopNavAgentButton({
  agentName,
  avatarUrl,
  dominantEmotion,
  stateThought,
  expanded,
  onClick,
}: TopNavAgentButtonProps) {
  const title = [agentName, dominantEmotion, stateThought]
    .filter(Boolean)
    .join(" - ");

  return (
    <div
      title={title}
      className="flex min-w-0 items-center gap-4 shrink-0"
    >
      <button
        onClick={onClick}
        className="relative size-12 overflow-hidden border border-border/70 shrink-0 hover:opacity-75 transition-opacity"
      >
        <img
          src={avatarUrl}
          alt={agentName}
          className="size-full object-cover"
        />
      </button>
      {expanded && (
        <span className="flex min-w-0 flex-col gap-1 pr-1">
          <button
            onClick={onClick}
            className="flex min-w-0 items-center gap-2 text-left hover:opacity-75 transition-opacity"
          >
            <span className="text-base font-semibold text-foreground leading-none truncate">
              {agentName}
            </span>
            {dominantEmotion && (
              <span
                className={cn(
                  "shrink-0 rounded-sm px-1.5 py-0.5 font-mono text-[8px] uppercase tracking-[0.12em] leading-none",
                  moodBadgeClass(dominantEmotion),
                )}
              >
                {dominantEmotion}
              </span>
            )}
          </button>
        </span>
      )}
    </div>
  );
}

export function LayoutTopNav() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { agentName, avatarUrl } = useAgentProfile(user?.id);
  const [agentState, setAgentState] = useState<AgentStateData | null>(null);
  const [showUser, setShowUser] = useState(false);
  const [expanded, setExpanded] = useState(true);

  const navRef = useRef<HTMLDivElement>(null);
  const userMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (user?.id == null) {
      setAgentState(null);
      return;
    }

    let active = true;
    api.consciousness.getAgentState(user.id)
      .then((data) => {
        if (active) setAgentState(data);
      })
      .catch(() => {
        if (active) setAgentState(null);
      });

    return () => {
      active = false;
    };
  }, [user?.id]);

  useEffect(() => {
    if (!showUser) return;
    const handler = (e: MouseEvent) => {
      if (
        userMenuRef.current &&
        !userMenuRef.current.contains(e.target as Node)
      ) {
        setShowUser(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showUser]);

  useEffect(() => {
    if (!expanded) return;
    const handler = (e: MouseEvent) => {
      if (navRef.current && !navRef.current.contains(e.target as Node)) {
        setExpanded(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [expanded]);



  const glassClasses = [
    "relative flex items-center gap-3 z-20 h-12",
    "bg-background/25",
    "backdrop-blur-[40px]",
    "border border-foreground/[0.08]",
    "shadow-[0_20px_50px_-12px_rgba(0,0,0,0.28)]",
    "before:absolute before:inset-0 before:pointer-events-none",
  ].join(" ");

  return (
    <header ref={navRef} className="w-full flex justify-between">
      <div className={cn("w-fit", glassClasses)}>
        <TopNavAgentButton
          agentName={agentName}
          avatarUrl={avatarUrl}
          dominantEmotion={agentState?.dominantEmotion ?? null}
          stateThought={agentState?.thought ?? null}
          expanded={expanded}
          onClick={() => navigate("/agent")}
        />
        <button
          onClick={() => setExpanded((v) => !v)}
          title={expanded ? "Collapse nav" : "Expand nav"}
          className="flex items-center justify-center w-7 h-7 shrink-0 text-muted-foreground/70 hover:text-foreground transition-colors"
        >
          {expanded ? (
            <ChevronRightIcon size="sm" />
          ) : (
            <ChevronLeftIcon size="sm" />
          )}
        </button>
      </div>
      {expanded && (
        <div className={cn(glassClasses)}>
          <nav className="flex items-center flex-1">
            {TOP_NAV_ITEMS.map(({ to, label, Icon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                title={label}
                onClick={() => setExpanded(false)}
                className={({ isActive }) =>
                  cn(
                    "group relative size-12 flex items-center justify-center transition-all duration-150",
                    isActive
                      ? "text-foreground bg-foreground/8"
                      : "text-foreground/35 hover:text-foreground/70 hover:bg-foreground/5",
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <Icon size="md" />
                    {isActive && (
                      <span className="absolute bottom-1 left-1/2 -translate-x-1/2 size-1 rounded-full bg-foreground/60" />
                    )}
                    <span
                      className={cn(
                        "pointer-events-none absolute top-full left-1/2 z-50 mt-1 -translate-x-1/2",
                        "rounded border border-border bg-background px-1.5 py-0.5",
                        "text-[10px] font-medium text-foreground shadow-sm",
                        "opacity-0 transition-opacity group-hover:opacity-100",
                      )}
                    >
                      {label}
                    </span>
                  </>
                )}
              </NavLink>
            ))}
          </nav>

        </div>
      )}
    </header>
  );
}
