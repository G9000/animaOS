import { useEffect, useRef, useState } from "react";
import { NavLink, useNavigate, useLocation } from "react-router-dom";
import { cn } from "@anima/standard-templates";
import type { AgentStateData } from "@anima/api-client";
import { useAuth } from "../../context/AuthContext";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { api } from "../../lib/api";
import { TOP_NAV_ITEMS } from "./nav-items";

// ── Time-of-day icon ──────────────────────────────────────────────────────────

// ── Mood badge ────────────────────────────────────────────────────────────────

const POSITIVE_MOODS = new Set([
  "happy",
  "excited",
  "hopeful",
  "grateful",
  "content",
  "playful",
  "affectionate",
  "calm",
]);
const NEGATIVE_MOODS = new Set([
  "sad",
  "lonely",
  "tired",
  "angry",
  "frustrated",
  "anxious",
  "worried",
]);

function moodColor(emotion: string) {
  const e = emotion.toLowerCase().trim();
  if (POSITIVE_MOODS.has(e)) return "text-accent border-accent/40";
  if (NEGATIVE_MOODS.has(e)) return "text-destructive border-destructive/40";
  return "text-foreground/50 border-foreground/[0.14]";
}

// ── Shared glass style ────────────────────────────────────────────────────────

const glass =
  "relative flex items-center z-20 h-16 " +
  "bg-background/20 backdrop-blur-[44px] " +
  "border border-foreground/[0.08] " +
  "shadow-[0_8px_32px_rgba(0,0,0,0.20)]";

// ── TopNavAgentButton (kept exported for any external consumers) ──────────────

export interface TopNavAgentButtonProps {
  agentName: string;
  avatarUrl: string;
  dominantEmotion: string | null;
  onClick: () => void;
}

export function TopNavAgentButton({
  agentName,
  avatarUrl,
  dominantEmotion,
  onClick,
}: TopNavAgentButtonProps) {
  return (
    <button
      onClick={onClick}
      className="group/agent flex items-center h-full hover:opacity-85 transition-opacity"
    >
      {/* Avatar — fills card height edge-to-edge */}
      <span className="relative h-full aspect-square shrink-0 overflow-hidden">
        <img
          src={avatarUrl}
          alt={agentName}
          className="h-full w-full object-cover"
        />
        <span className="absolute inset-0 bg-accent/0 group-hover/agent:bg-accent/10 transition-colors" />
      </span>

      {/* Divider */}
      <span className="w-px h-5 bg-foreground/[0.10] shrink-0" />

      {/* Name + emotion */}
      <span className="flex flex-col gap-[5px] min-w-0 px-3">
        <span className="font-semibold text-[14px] leading-none text-foreground truncate max-w-[110px]">
          {agentName}
        </span>
        {dominantEmotion && (
          <span
            className={cn(
              "font-mono text-[7.5px] uppercase tracking-[0.16em] leading-none border px-1.5 py-[3px] w-fit",
              moodColor(dominantEmotion),
            )}
          >
            {dominantEmotion}
          </span>
        )}
      </span>
    </button>
  );
}

// ── Main nav ──────────────────────────────────────────────────────────────────

export function LayoutHUD() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const { agentName, avatarUrl } = useAgentProfile(user?.id);
  const activeNavItem = TOP_NAV_ITEMS.find((item) =>
    item.to === "/"
      ? location.pathname === "/"
      : location.pathname.startsWith(item.to),
  );
  const [agentState, setAgentState] = useState<AgentStateData | null>(null);
  const [time, setTime] = useState(() =>
    new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }).toUpperCase()
  );
  const [collapsed] = useState(() => {
    try {
      return localStorage.getItem("anima_nav_collapsed") === "true";
    } catch {
      return false;
    }
  });
  const navRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const id = setInterval(() =>
      setTime(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }).toUpperCase()),
      30_000
    );
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (user?.id == null) {
      setAgentState(null);
      return;
    }
    let active = true;
    api.consciousness
      .getAgentState(user.id)
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

  return (
    <header ref={navRef} className="relative w-full flex justify-between items-stretch">
      {/* ── Left: Agent identity + clock ── */}
      <div className={cn(glass, "group/leftcard")}>
        <button
          onClick={() => navigate("/agent")}
          className="group/avatar relative h-full aspect-square shrink-0 overflow-hidden hover:opacity-85 transition-opacity"
        >
          <img
            src={avatarUrl}
            alt={agentName}
            className="h-full w-full object-cover"
          />
          <span className="absolute inset-0 bg-accent/0 group-hover/avatar:bg-accent/10 transition-colors" />
        </button>

        {/* Name + mood — hidden when collapsed */}
        {!collapsed && (
          <>
            <button
              onClick={() => navigate("/agent")}
              className="group/name grid gap-1 min-w-0 px-3"
            >
              <span className="font-mono text-base font-semibold tracking-[0.16em] text-foreground/60 group-hover/leftcard:text-accent uppercase leading-none truncate max-w-[130px] transition-colors duration-200">
                {agentName}
              </span>
              {agentState?.dominantEmotion && (
                <span
                  className={cn(
                    "font-mono text-[7.5px] uppercase tracking-[0.16em] leading-none border px-1.5 py-[3px] w-fit",
                    "transition-colors duration-200",
                    "group-hover/name:bg-accent group-hover/name:text-accent-foreground group-hover/name:border-accent",
                    "group-hover/leftcard:border-accent/60 group-hover/leftcard:text-accent",
                    moodColor(agentState.dominantEmotion),
                  )}
                >
                  {agentState.dominantEmotion}
                </span>
              )}
            </button>
          </>
        )}

        {/* <InlineClock /> */}

        {/* Collapse toggle — far right of left card */}
        {/* <button
          onClick={toggleCollapsed}
          className="flex items-center justify-center w-6 h-full shrink-0 text-foreground/20 group-hover/leftcard:text-accent group-hover/leftcard:border-accent/20 hover:bg-foreground/[0.04] transition-colors border-l border-foreground/[0.06]"
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed
            ? <ChevronRightIcon size="sm" />
            : <ChevronLeftIcon size="sm" />
          }
        </button> */}
      </div>

      {/* ── Right: Navigation — hidden when collapsed ── */}
      {!collapsed && (
        <div className="relative flex flex-col items-end w-fit">
          <div
            style={{
              clipPath:
                "polygon(0 0, 100% 0, 100% 100%, 12px 100%, 0 calc(100% - 12px))",
            }}
            className={cn(glass, "p-0 overflow-visible")}
          >
            {TOP_NAV_ITEMS.map(({ to, Icon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                style={({ isActive }) => ({
                  ...(isActive
                    ? {
                        transform: "translate(-2px, -4px)",
                        boxShadow:
                          "2px 4px 0 var(--color-accent-dark), 4px 8px 0 color-mix(in oklch, var(--color-accent-dark) 40%, transparent)",
                      }
                    : {}),
                })}
                className={({ isActive }) =>
                  cn(
                    "group/nav relative size-16 grid items-center justify-center gap-2 z-10",
                    "transition-[transform,box-shadow,filter] duration-150",
                    isActive
                      ? "bg-accent text-accent-foreground"
                      : "text-foreground/30 hover:text-accent hover:bg-accent/20",
                  )
                }
                onMouseEnter={(e) => {
                  const el = e.currentTarget as HTMLElement;
                  el.style.transform = "translate(-2px, -4px)";
                  el.style.boxShadow =
                    "2px 4px 0 var(--color-accent-dark), 4px 8px 0 color-mix(in oklch, var(--color-accent-dark) 40%, transparent)";
                }}
                onMouseLeave={(e) => {
                  const el = e.currentTarget as HTMLElement;
                  if (el.classList.contains("bg-accent")) return;
                  el.style.transform = "";
                  el.style.boxShadow = "";
                }}
              >
                {() => (
                  <>
                    <Icon size="sm" />
                  </>
                )}
              </NavLink>
            ))}
          </div>
        </div>
      )}

      {activeNavItem && (
        <div
          style={{ clipPath: "polygon(12px 0, 100% 0, 100% 100%, 0 100%, 0 12px)" }}
          className="fixed bottom-0 right-0 z-30 bg-accent px-4 py-1.5 pointer-events-none flex flex-col items-end gap-0.5"
        >
          <span className="font-mono text-ui uppercase text-accent-foreground/90 leading-none">
            {activeNavItem.description}
          </span>
        </div>
      )}
    </header>
  );
}
