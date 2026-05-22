import { useCallback, useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  HomeIcon,
  TasksIcon,
  ChatIcon,
  MemoryIcon,
  ProactivityIcon,
  MindIcon,
  ModsIcon,
  ConfigIcon,
  DatabaseIcon,
  cn,
  type IconProps,
} from "@anima/standard-templates";
import { useAuth } from "../../context/AuthContext";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { SETTINGS_CHANGED_EVENT } from "../../lib/events";
import { getDbViewerEnabled } from "../../lib/preferences";
import { getTheme, toggleTheme, type Theme } from "../../lib/theme";

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
  { to: "/proactivity", label: "Proactivity", Icon: ProactivityIcon, description: "signals" },
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
  const { agentName, avatarUrl } = useAgentProfile(user?.id);

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

  const navItems = dbEnabled ? [...STATIC_NAV_ITEMS, DATABASE_NAV_ITEM] : STATIC_NAV_ITEMS;

  return (
    <aside
      className={cn(
        "relative flex-shrink-0 flex flex-col border-r border-border bg-sidebar overflow-hidden",
        "transition-[width] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)]",
        collapsed ? "w-14" : "w-60",
      )}
    >
      {/* Agent header — full-width avatar */}
      <div className={cn("flex-shrink-0 border-b border-border", !collapsed && "p-2")}>
        <button
          onClick={handleAgentClick}
          title={collapsed ? agentName : `${agentName} — click to edit`}
          className={cn(
            "relative w-full overflow-hidden bg-card hover:opacity-90 transition-opacity",
            !collapsed && "rounded-md",
          )}
        >
          <img
            src={avatarUrl}
            alt={agentName}
            className={cn("w-full object-cover", collapsed ? "aspect-square" : "aspect-[4/3] rounded-md")}
          />
          {!collapsed && (
            <div className="absolute bottom-0 left-0 right-0 p-2">
              <span className="inline-block bg-sidebar/90 backdrop-blur-sm border border-border px-2.5 py-1 text-caption font-mono tracking-[0.18em] uppercase text-foreground rounded-sm">
                {agentName}
              </span>
            </div>
          )}
        </button>
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
                  ? "bg-secondary text-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-secondary/50",
              )
            }
          >
            {({ isActive }) => (
              <>
                <span
                  className={cn(
                    "flex-shrink-0 flex items-center justify-center transition-colors",
                    collapsed ? "w-5 h-5" : "w-[18px] h-[18px]",
                    isActive
                      ? "text-foreground"
                      : "text-muted-foreground group-hover:text-foreground",
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
            <span className="text-xs leading-none">{theme === "dark" ? "☀" : "☾"}</span>
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
            <span className="text-xs leading-none font-mono">{collapsed ? "→" : "←"}</span>
            {!collapsed && (
              <span className="text-caption font-mono tracking-[0.16em] uppercase">Collapse</span>
            )}
          </button>
        </div>

        {/* User */}
        <div className={cn("relative", !collapsed && "px-1.5 pb-1.5")}>
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
                "absolute bottom-full border border-border bg-sidebar z-50 overflow-hidden shadow-lg",
                collapsed ? "left-full ml-1 min-w-[148px]" : "left-0 right-0",
              )}
              onMouseLeave={() => setShowUser(false)}
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
