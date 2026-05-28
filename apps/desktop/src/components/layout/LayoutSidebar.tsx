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
  ChevronRightIcon,
  cn,
  type IconProps,
} from "@anima/standard-templates";
import { useAuth } from "../../context/AuthContext";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { api } from "../../lib/api";
import { SETTINGS_CHANGED_EVENT } from "../../lib/events";
import { getDbViewerEnabled } from "../../lib/preferences";
import { getTheme, toggleTheme, type Theme } from "../../lib/theme";


const POSITIVE_MOODS = new Set(["happy", "excited", "hopeful", "grateful", "content", "playful", "affectionate", "calm"]);
const NEGATIVE_MOODS = new Set(["sad", "lonely", "tired", "angry", "frustrated", "anxious", "worried"]);

function moodBadgeClass(emotion: string): string {
  const e = emotion.toLowerCase().trim();
  if (POSITIVE_MOODS.has(e)) return "bg-accent/30 text-accent border border-accent/30";
  if (NEGATIVE_MOODS.has(e)) return "bg-destructive/20 text-destructive border border-destructive/20";
  return "bg-border text-foreground/70 border border-border";
}

function moodDotClass(emotion: string): string {
  const e = emotion.toLowerCase().trim();
  if (POSITIVE_MOODS.has(e)) return "bg-accent ring-2 ring-sidebar ring-offset-0";
  if (NEGATIVE_MOODS.has(e)) return "bg-destructive ring-2 ring-sidebar ring-offset-0";
  return "bg-muted-foreground ring-2 ring-sidebar ring-offset-0";
}

interface NavItem {
  to: string;
  label: string;
  Icon: React.ComponentType<IconProps>;
  description: string;
}

const STATIC_NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Home", Icon: HomeIcon, description: "dashboard" },
  { to: "/tasks", label: "Tasks", Icon: TasksIcon, description: "queue" },
  { to: "/chat", label: "Chat", Icon: ChatIcon, description: "console" },
  { to: "/memory", label: "Memory", Icon: MemoryIcon, description: "archive" },
  { to: "/presence", label: "Presence", Icon: PresenceIcon, description: "signals" },
  { to: "/consciousness", label: "Mind", Icon: MindIcon, description: "consciousness" },
  { to: "/mods", label: "Mods", Icon: ModsIcon, description: "extensions" },
  { to: "/settings", label: "Settings", Icon: ConfigIcon, description: "system" },
];

const DATABASE_NAV_ITEM: NavItem = {
  to: "/database",
  label: "Database",
  Icon: DatabaseIcon,
  description: "inspector",
};

const SIDEBAR_STORAGE_KEY = "anima-sidebar-collapsed";

function readCollapsedState(): boolean {
  try {
    return localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function persistCollapsedState(collapsed: boolean): void {
  try {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, String(collapsed));
  } catch {
    // ignore
  }
}

export function LayoutSidebar() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [dbEnabled, setDbEnabled] = useState(getDbViewerEnabled);
  const [collapsed, setCollapsed] = useState(readCollapsedState);
  const [showUser, setShowUser] = useState(false);
  const [theme, setTheme] = useState<Theme>(getTheme);
  const { agentName, avatarUrl, relationship } = useAgentProfile(user?.id);
  const [dominantEmotion, setDominantEmotion] = useState<string | null>(null);
  const userMenuRef = useRef<HTMLDivElement>(null);

  const handleAgentClick = useCallback(() => navigate("/agent"), [navigate]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((current) => {
      const next = !current;
      persistCollapsedState(next);
      return next;
    });
  }, []);

  const syncDbViewer = useCallback(() => setDbEnabled(getDbViewerEnabled()), []);

  useEffect(() => {
    window.addEventListener(SETTINGS_CHANGED_EVENT, syncDbViewer);
    return () => window.removeEventListener(SETTINGS_CHANGED_EVENT, syncDbViewer);
  }, [syncDbViewer]);

  useEffect(() => {
    const handleShortcut = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "/") {
        e.preventDefault();
        toggleCollapsed();
      }
    };
    window.addEventListener("keydown", handleShortcut);
    return () => window.removeEventListener("keydown", handleShortcut);
  }, [toggleCollapsed]);

  useEffect(() => {
    if (user?.id == null) return;
    let active = true;
    api.consciousness.getEmotions(user.id, 1)
      .then((data) => { if (active) setDominantEmotion(data.dominantEmotion); })
      .catch(() => {});
    return () => { active = false; };
  }, [user?.id]);

  useEffect(() => {
    if (!showUser) return;
    function handleClickOutside(e: MouseEvent) {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setShowUser(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [showUser]);

  const navItems = dbEnabled ? [...STATIC_NAV_ITEMS, DATABASE_NAV_ITEM] : STATIC_NAV_ITEMS;

  return (
    <aside
      className={cn(
        "relative flex-shrink-0 flex flex-col border-r border-border bg-sidebar overflow-hidden",
        "transition-[width] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)]",
        collapsed ? "w-14" : "w-60",
      )}
    >
      {/* Agent header */}
      <div className="flex-shrink-0 border-b border-border">
        {collapsed ? (
          <button
            onClick={handleAgentClick}
            title={dominantEmotion ? `${agentName} · ${dominantEmotion}` : agentName}
            className="relative w-full overflow-hidden hover:opacity-90 transition-opacity"
          >
            <img src={avatarUrl} alt={agentName} className="w-full aspect-square object-cover" />
            {dominantEmotion && (
              <span className={cn("absolute bottom-2 right-2 w-2 h-2 rounded-full", moodDotClass(dominantEmotion))} />
            )}
          </button>
        ) : (
          <div className="p-2 space-y-2">
            <button
              onClick={handleAgentClick}
              title={`${agentName} — click to edit`}
              className="relative w-full overflow-hidden rounded-sm ring-1 ring-border/50 hover:ring-border transition-all"
            >
              <img src={avatarUrl} alt={agentName} className="w-full aspect-[4/3] object-cover" />
            </button>
            <div className="flex items-center justify-between px-0.5">
              <div className="flex flex-col gap-1 min-w-0">
                <span className="text-body font-medium text-foreground leading-none truncate">
                  {agentName}
                </span>
                {relationship && (
                  <span className="font-mono text-[8px] tracking-[0.14em] uppercase text-muted-foreground/40 leading-none">
                    {relationship}
                  </span>
                )}
              </div>
              {dominantEmotion && (
                <span className={cn(
                  "flex-shrink-0 font-mono text-[8px] tracking-[0.12em] uppercase px-1.5 py-0.5 rounded-sm ml-2",
                  moodBadgeClass(dominantEmotion),
                )}>
                  {dominantEmotion}
                </span>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto overflow-x-hidden py-3 px-1.5 space-y-0.5">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            title={collapsed ? `${item.label} — ${item.description}` : undefined}
            className={({ isActive }) =>
              cn(
                "group flex items-center transition-colors duration-100 rounded-md",
                collapsed ? "justify-center px-0 py-2.5 mx-1" : "gap-3 px-3 py-2.5",
                isActive
                  ? "bg-foreground/[0.12] text-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary/50",
              )
            }
          >
            {({ isActive }) => (
              <>
                <span
                  className={cn(
                    "flex-shrink-0 flex items-center justify-center transition-colors",
                    isActive ? "text-foreground" : "text-muted-foreground group-hover:text-foreground",
                  )}
                >
                  <item.Icon size="sm" />
                </span>
                {!collapsed && (
                  <span className="text-ui leading-none font-medium">{item.label}</span>
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Footer controls */}
      <div className="flex-shrink-0 border-t border-border">
        {/* Theme + Collapse row */}
        <div className={cn("flex gap-1 p-1.5", collapsed ? "flex-col" : "flex-row")}>
          <button
            onClick={() => setTheme(toggleTheme())}
            title={theme === "dark" ? "Switch to light" : "Switch to dark"}
            className={cn(
              "flex items-center justify-center gap-2 py-2 text-muted-foreground hover:text-foreground hover:bg-secondary/50 transition-colors rounded-md",
              collapsed ? "w-full" : "flex-1",
            )}
          >
            <span className="text-xs leading-none select-none">{theme === "dark" ? "☀" : "☾"}</span>
            {!collapsed && (
              <span className="text-caption font-mono tracking-[0.16em] uppercase">
                {theme === "dark" ? "Light" : "Dark"}
              </span>
            )}
          </button>

          <button
            onClick={toggleCollapsed}
            title={collapsed ? "Expand (Ctrl+/)" : "Collapse (Ctrl+/)"}
            className={cn(
              "flex items-center justify-center gap-2 py-2 text-muted-foreground hover:text-foreground hover:bg-secondary/50 transition-colors rounded-md",
              collapsed ? "w-full" : "flex-1",
            )}
          >
            <ChevronRightIcon
              size="sm"
              className={cn("transition-transform duration-200", !collapsed && "rotate-180")}
            />
            {!collapsed && (
              <span className="text-caption font-mono tracking-[0.16em] uppercase">Collapse</span>
            )}
          </button>
        </div>

        {/* User */}
        <div ref={userMenuRef} className={cn("relative", !collapsed && "px-1.5 pb-1.5")}>
          <button
            onClick={() => setShowUser((v) => !v)}
            className={cn(
              "w-full flex items-center gap-2.5 text-muted-foreground hover:text-foreground hover:bg-secondary/50 transition-colors rounded-md",
              collapsed ? "justify-center px-0 py-2.5 mx-1" : "px-3 py-2",
            )}
          >
            <span className="flex-shrink-0 flex items-center justify-center w-6 h-6 border border-border/60 bg-card font-mono text-caption font-bold uppercase rounded-sm">
              {user?.name?.charAt(0) || "?"}
            </span>
            {!collapsed && (
              <span className="text-ui truncate flex-1 text-left">
                {user?.name || "User"}
              </span>
            )}
          </button>

          {showUser && (
            <div
              className={cn(
                "absolute bottom-full border border-border bg-sidebar z-50 overflow-hidden shadow-lg rounded-md",
                collapsed ? "left-full ml-1 min-w-[148px]" : "left-0 right-0",
              )}
            >
              <div className="px-3 py-2 border-b border-border">
                <div className="text-caption text-muted-foreground truncate">
                  {user?.name || "Guest"}
                </div>
              </div>
              <button
                onClick={() => { navigate("/profile"); setShowUser(false); }}
                className="w-full text-left px-3 py-2.5 text-caption text-muted-foreground hover:text-foreground hover:bg-secondary/50 transition-colors border-b border-border/50"
              >
                Profile
              </button>
              <button
                onClick={() => { setShowUser(false); void logout().then(() => navigate("/login")); }}
                className="w-full text-left px-3 py-2.5 text-caption text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
              >
                Log out
              </button>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}
