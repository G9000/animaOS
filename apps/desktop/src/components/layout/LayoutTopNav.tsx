import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  HomeIcon,
  TasksIcon,
  ChatIcon,
  MemoryIcon,
  PresenceIcon,
  MindIcon,
  ModsIcon,
  ConfigIcon,
  DatabaseIcon,
  cn,
  type IconProps,
} from "@anima/standard-templates";
import type { MemoryOverviewData } from "@anima/api-client";
import { useAuth } from "../../context/AuthContext";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { api } from "../../lib/api";
import { SETTINGS_CHANGED_EVENT } from "../../lib/events";
import { getDbViewerEnabled } from "../../lib/preferences";
import { getTheme, toggleTheme, type Theme } from "../../lib/theme";

const POSITIVE_MOODS = new Set(["happy", "excited", "hopeful", "grateful", "content", "playful", "affectionate", "calm"]);
const NEGATIVE_MOODS = new Set(["sad", "lonely", "tired", "angry", "frustrated", "anxious", "worried"]);

function moodDotClass(emotion: string): string {
  const e = emotion.toLowerCase().trim();
  if (POSITIVE_MOODS.has(e)) return "bg-accent";
  if (NEGATIVE_MOODS.has(e)) return "bg-destructive";
  return "bg-muted-foreground";
}

interface NavItem {
  to: string;
  label: string;
  Icon: React.ComponentType<IconProps>;
}

const STATIC_NAV_ITEMS: NavItem[] = [
  { to: "/",             label: "Home",     Icon: HomeIcon     },
  { to: "/chat",         label: "Chat",     Icon: ChatIcon     },
  { to: "/memory",       label: "Memory",   Icon: MemoryIcon   },
  { to: "/consciousness",label: "Mind",     Icon: MindIcon     },
  { to: "/tasks",        label: "Tasks",    Icon: TasksIcon    },
  { to: "/presence",     label: "Presence", Icon: PresenceIcon },
  { to: "/mods",         label: "Mods",     Icon: ModsIcon     },
  { to: "/settings",     label: "Settings", Icon: ConfigIcon   },
];

const DATABASE_NAV_ITEM: NavItem = { to: "/database", label: "Database", Icon: DatabaseIcon };

export function LayoutTopNav() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const { agentName, avatarUrl } = useAgentProfile(user?.id);
  const [theme, setTheme] = useState<Theme>(getTheme);
  const [showUser, setShowUser] = useState(false);
  const [dbEnabled, setDbEnabled] = useState(getDbViewerEnabled);
  const [dominantEmotion, setDominantEmotion] = useState<string | null>(null);
  const [memOverview, setMemOverview] = useState<MemoryOverviewData | null>(null);
  const [pendingTasks, setPendingTasks] = useState<number | null>(null);
  const userMenuRef = useRef<HTMLDivElement>(null);

  const syncDbViewer = useCallback(() => setDbEnabled(getDbViewerEnabled()), []);

  useEffect(() => {
    window.addEventListener(SETTINGS_CHANGED_EVENT, syncDbViewer);
    return () => window.removeEventListener(SETTINGS_CHANGED_EVENT, syncDbViewer);
  }, [syncDbViewer]);

  useEffect(() => {
    if (user?.id == null) return;
    let active = true;
    api.consciousness.getEmotions(user.id, 1)
      .then((data) => { if (active) setDominantEmotion(data.dominantEmotion); })
      .catch(() => {});
    return () => { active = false; };
  }, [user?.id]);

  useEffect(() => {
    if (user?.id == null) return;
    let active = true;
    api.memory.overview(user.id)
      .then((data) => { if (active) setMemOverview(data); })
      .catch(() => {});
    return () => { active = false; };
  }, [user?.id]);

  useEffect(() => {
    if (user?.id == null) return;
    let active = true;
    api.tasks.list(user.id)
      .then((list) => { if (active) setPendingTasks((list ?? []).filter((t) => !t.done).length); })
      .catch(() => {});
    return () => { active = false; };
  }, [user?.id]);

  useEffect(() => {
    if (!showUser) return;
    const handler = (e: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setShowUser(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showUser]);

  const navItems = dbEnabled ? [...STATIC_NAV_ITEMS, DATABASE_NAV_ITEM] : STATIC_NAV_ITEMS;

  return (
    <header className="h-12 flex items-center border border-border/70 bg-background/90 backdrop-blur-md shadow-md px-4 gap-3 z-20">
      {/* Agent branding */}
      <button
        onClick={() => navigate("/agent")}
        className="flex items-center gap-2 shrink-0 hover:opacity-75 transition-opacity"
      >
        <div className="relative w-7 h-7 rounded-full overflow-hidden border border-border/70">
          <img src={avatarUrl} alt={agentName} className="w-full h-full object-cover" />
          {dominantEmotion && (
            <span className={cn("absolute bottom-0 right-0 w-2 h-2 rounded-full border border-background", moodDotClass(dominantEmotion))} />
          )}
        </div>
        <span className="text-sm font-semibold text-foreground leading-none">{agentName}</span>
      </button>

      <div className="w-px h-4 bg-border/60 shrink-0" />

      {/* Navigation */}
      <nav className="flex items-center gap-0.5 flex-1">
        {navItems.map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            title={label}
            className={({ isActive }) =>
              cn(
                "relative w-9 h-9 flex items-center justify-center transition-all duration-150",
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
                  <span className="absolute bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 rounded-full bg-foreground/60" />
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Agent stats */}
      <div className="flex items-center gap-px shrink-0 border border-border/50 bg-muted/30">
        {memOverview != null && (
          <div className="flex flex-col items-center px-3 py-1 border-r border-border/40">
            <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/40">MEM</span>
            <span className="font-mono text-[11px] font-medium text-foreground/80 tabular-nums leading-tight">{memOverview.totalItems}</span>
          </div>
        )}
        {memOverview != null && (
          <div className="flex flex-col items-center px-3 py-1 border-r border-border/40">
            <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/40">EPS</span>
            <span className="font-mono text-[11px] font-medium text-foreground/80 tabular-nums leading-tight">{memOverview.episodeCount}</span>
          </div>
        )}
        {pendingTasks != null && (
          <div className="flex flex-col items-center px-3 py-1 border-r border-border/40">
            <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/40">TASKS</span>
            <span className={cn("font-mono text-[11px] font-medium tabular-nums leading-tight", pendingTasks > 0 ? "text-accent" : "text-foreground/40")}>{pendingTasks}</span>
          </div>
        )}
        {dominantEmotion && (
          <div className="flex flex-col items-center px-3 py-1">
            <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/40">STATE</span>
            <span className="font-mono text-[11px] font-medium text-foreground/80 leading-tight capitalize">{dominantEmotion}</span>
          </div>
        )}
      </div>

      {/* Right controls */}
      <div className="flex items-center gap-0.5 shrink-0">
        <button
          onClick={() => setTheme(toggleTheme())}
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
          className="w-8 h-8 flex items-center justify-center text-muted-foreground/60 hover:text-foreground hover:bg-secondary/50 transition-colors"
        >
          <span className="text-sm leading-none select-none">
            {theme === "dark" ? "☀" : "☾"}
          </span>
        </button>

        <div ref={userMenuRef} className="relative">
          <button
            onClick={() => setShowUser((v) => !v)}
            className="w-8 h-8 flex items-center justify-center border border-border font-mono text-xs font-medium text-foreground/70 hover:bg-secondary/50 transition-colors"
          >
            {user?.name?.[0]?.toUpperCase() ?? "U"}
          </button>
          {showUser && (
            <div className="absolute right-0 top-full mt-1 w-44 border border-border bg-card shadow-xl z-50">
              <div className="px-3 py-2.5 border-b border-border/60">
                <p className="text-sm font-medium text-foreground truncate">{user?.name}</p>
              </div>
              <button
                onClick={() => { navigate("/profile"); setShowUser(false); }}
                className="w-full text-left px-3 py-2 text-sm text-muted-foreground hover:text-foreground hover:bg-secondary/50 transition-colors"
              >
                Profile
              </button>
              <button
                onClick={() => { logout(); setShowUser(false); }}
                className="w-full text-left px-3 py-2 text-sm text-destructive hover:bg-destructive/5 transition-colors"
              >
                Log out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
