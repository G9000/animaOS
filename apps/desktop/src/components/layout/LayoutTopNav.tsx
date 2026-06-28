import { useEffect, useRef, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  SunIcon, MoonIcon, SunriseIcon, SunsetIcon,
  ChevronRightIcon, ChevronLeftIcon, cn,
} from "@anima/standard-templates";
import type { AgentStateData } from "@anima/api-client";
import { useAuth } from "../../context/AuthContext";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { useClockFormat } from "../../hooks/useClockFormat";
import { api } from "../../lib/api";
import { TOP_NAV_ITEMS } from "./nav-items";

// ── Time-of-day icon ──────────────────────────────────────────────────────────

function DayIcon({ hour }: { hour: number }) {
  if (hour < 5 || hour >= 21) return <MoonIcon size="sm" />;
  if (hour < 9)               return <SunriseIcon size="sm" />;
  if (hour >= 18)             return <SunsetIcon size="sm" />;
  return                             <SunIcon size="sm" />;
}

// ── Inline clock ─────────────────────────────────────────────────────────────

function InlineClock() {
  const [now, setNow] = useState(() => new Date());
  const { format } = useClockFormat();

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(id);
  }, []);

  const time = now
    .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: format === "12h" })
    .toUpperCase();
  const day  = now.toLocaleDateString([], { weekday: "short" }).toUpperCase();
  const date = now.toLocaleDateString([], { day: "numeric", month: "short" }).toUpperCase();

  return (
    <div className="flex items-center gap-3 shrink-0 pl-2 pr-4 cursor-default">
      <div className="w-px h-6 bg-foreground/[0.10] group-hover/leftcard:bg-accent/40 transition-colors duration-200" />
      <div className="flex items-center gap-2.5 select-none">
        <span className="text-foreground/35 group-hover/leftcard:text-accent shrink-0 transition-colors duration-200">
          <DayIcon hour={now.getHours()} />
        </span>
        <div className="flex flex-col gap-[5px]">
          <span className="font-mono text-[15px] tracking-[0.16em] text-foreground/60 group-hover/leftcard:text-accent uppercase leading-none transition-colors duration-200">
            {time}
          </span>
          <span className="font-mono text-[10px] tracking-[0.20em] text-foreground/32 group-hover/leftcard:text-accent/70 leading-none transition-colors duration-200">
            {day} · {date}
          </span>
        </div>
      </div>
    </div>
  );
}

// ── Mood badge ────────────────────────────────────────────────────────────────

const POSITIVE_MOODS = new Set(["happy", "excited", "hopeful", "grateful", "content", "playful", "affectionate", "calm"]);
const NEGATIVE_MOODS = new Set(["sad", "lonely", "tired", "angry", "frustrated", "anxious", "worried"]);

function moodColor(emotion: string) {
  const e = emotion.toLowerCase().trim();
  if (POSITIVE_MOODS.has(e)) return "text-accent border-accent/40";
  if (NEGATIVE_MOODS.has(e)) return "text-destructive border-destructive/40";
  return "text-foreground/50 border-foreground/[0.14]";
}

// ── Shared glass style ────────────────────────────────────────────────────────

const glass =
  "relative flex items-center z-20 h-10 " +
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

export function TopNavAgentButton({ agentName, avatarUrl, dominantEmotion, onClick }: TopNavAgentButtonProps) {
  return (
    <button
      onClick={onClick}
      className="group/agent flex items-center h-full hover:opacity-85 transition-opacity"
    >
      {/* Avatar — fills card height edge-to-edge */}
      <span className="relative h-full aspect-square shrink-0 overflow-hidden">
        <img src={avatarUrl} alt={agentName} className="h-full w-full object-cover" />
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
          <span className={cn(
            "font-mono text-[7.5px] uppercase tracking-[0.16em] leading-none border px-1.5 py-[3px] w-fit",
            moodColor(dominantEmotion),
          )}>
            {dominantEmotion}
          </span>
        )}
      </span>
    </button>
  );
}

// ── Main nav ──────────────────────────────────────────────────────────────────

export function LayoutTopNav() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { agentName, avatarUrl } = useAgentProfile(user?.id);
  const [agentState, setAgentState] = useState<AgentStateData | null>(null);
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem("anima_nav_collapsed") === "true"; } catch { return false; }
  });
  const navRef = useRef<HTMLDivElement>(null);

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    try { localStorage.setItem("anima_nav_collapsed", String(next)); } catch {}
  };

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
    <header ref={navRef} className="w-full flex justify-between items-stretch">

      {/* ── Left: Agent identity + clock ── */}
      <div className={cn(glass, "group/leftcard")}>
        {/* Avatar — always visible, click navigates to agent */}
        <button
          onClick={() => navigate("/agent")}
          className="group/avatar relative h-full aspect-square shrink-0 overflow-hidden hover:opacity-85 transition-opacity"
        >
          <img src={avatarUrl} alt={agentName} className="h-full w-full object-cover" />
          <span className="absolute inset-0 bg-accent/0 group-hover/avatar:bg-accent/10 transition-colors" />
        </button>

        {/* Name + mood — hidden when collapsed */}
        {!collapsed && (
          <>
            <span className="w-px h-5 bg-foreground/[0.10] group-hover/leftcard:bg-accent/40 shrink-0 ml-1 transition-colors duration-200" />
            <button
              onClick={() => navigate("/agent")}
              className="group/name flex flex-col gap-[6px] min-w-0 px-3"
            >
              <span className="font-mono text-[15px] tracking-[0.16em] text-foreground/60 group-hover/leftcard:text-accent uppercase leading-none truncate max-w-[130px] transition-colors duration-200">
                {agentName}
              </span>
              {agentState?.dominantEmotion && (
                <span className={cn(
                  "font-mono text-[7.5px] uppercase tracking-[0.16em] leading-none border px-1.5 py-[3px] w-fit",
                  "transition-colors duration-200",
                  "group-hover/name:bg-accent group-hover/name:text-accent-foreground group-hover/name:border-accent",
                  "group-hover/leftcard:border-accent/60 group-hover/leftcard:text-accent",
                  moodColor(agentState.dominantEmotion),
                )}>
                  {agentState.dominantEmotion}
                </span>
              )}
            </button>
          </>
        )}

        <InlineClock />

        {/* Collapse toggle — far right of left card */}
        <button
          onClick={toggleCollapsed}
          className="flex items-center justify-center w-6 h-full shrink-0 text-foreground/20 group-hover/leftcard:text-accent group-hover/leftcard:border-accent/20 hover:bg-foreground/[0.04] transition-colors border-l border-foreground/[0.06]"
          title={collapsed ? "Expand" : "Collapse"}
        >
          {collapsed
            ? <ChevronRightIcon size="sm" />
            : <ChevronLeftIcon size="sm" />
          }
        </button>
      </div>

      {/* ── Right: Navigation — hidden when collapsed ── */}
      {!collapsed && <div className={cn(glass, "overflow-hidden p-0")}>
        {TOP_NAV_ITEMS.map(({ to, label, Icon, description }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cn(
                "group/nav relative size-10 grid items-center justify-center gap-2 transition-colors duration-150",
                isActive
                  ? "bg-accent text-accent-foreground"
                  : "text-foreground/30 hover:text-foreground/80 hover:bg-foreground/[0.06]",
              )
            }
          >
            {({ isActive }) => (
              <>
                <Icon size="sm" />
                {/* <span className={cn(
                  "font-mono text-[7.5px] tracking-[0.14em] uppercase leading-none transition-colors",
                  isActive
                    ? "text-accent-foreground/60"
                    : "text-foreground/20 group-hover/nav:text-foreground/50",
                )}>
                  {description}
                </span> */}
                {/* Tooltip */}
                <span className={cn(
                  "pointer-events-none absolute top-full left-1/2 z-50 mt-1.5 -translate-x-1/2 whitespace-nowrap",
                  "border border-foreground/[0.08] bg-background/80 backdrop-blur-sm px-2 py-1",
                  "font-mono text-[9px] tracking-[0.1em] text-foreground/60",
                  "opacity-0 transition-opacity group-hover/nav:opacity-100",
                )}>
                  {label}
                </span>
              </>
            )}
          </NavLink>
        ))}

      </div>}

    </header>
  );
}
