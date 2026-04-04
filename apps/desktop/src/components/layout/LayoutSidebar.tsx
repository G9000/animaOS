import { useCallback, useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  HomeIcon,
  TasksIcon,
  ChatIcon,
  MemoryIcon,
  MindIcon,
  ModsIcon,
  ConfigIcon,
  DatabaseIcon,
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
  { to: "/", label: "HOME", Icon: HomeIcon, description: "dashboard" },
  { to: "/tasks", label: "TASKS", Icon: TasksIcon, description: "queue" },
  { to: "/chat", label: "CHAT", Icon: ChatIcon, description: "console" },
  { to: "/memory", label: "MEM", Icon: MemoryIcon, description: "archive" },
  {
    to: "/consciousness",
    label: "MIND",
    Icon: MindIcon,
    description: "consciousness",
  },
  { to: "/mods", label: "MODS", Icon: ModsIcon, description: "extensions" },
  { to: "/settings", label: "CFG", Icon: ConfigIcon, description: "system" },
];

const DATABASE_NAV_ITEM: NavItem = {
  to: "/database",
  label: "DB",
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
    // Ignore storage failures.
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

  const toggleCollapsed = useCallback(() => {
    setCollapsed((current) => {
      const next = !current;
      persistCollapsedState(next);
      return next;
    });
  }, []);

  const syncDbViewer = useCallback(() => {
    setDbEnabled(getDbViewerEnabled());
  }, []);

  useEffect(() => {
    window.addEventListener(SETTINGS_CHANGED_EVENT, syncDbViewer);
    return () => {
      window.removeEventListener(SETTINGS_CHANGED_EVENT, syncDbViewer);
    };
  }, [syncDbViewer]);

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "/") {
        event.preventDefault();
        toggleCollapsed();
      }
    };

    window.addEventListener("keydown", handleShortcut);
    return () => {
      window.removeEventListener("keydown", handleShortcut);
    };
  }, [toggleCollapsed]);

  const navItems = dbEnabled
    ? [...STATIC_NAV_ITEMS, DATABASE_NAV_ITEM]
    : STATIC_NAV_ITEMS;

  const agentLabel = agentName.toUpperCase();

  return (
    <aside
      className={`relative flex-shrink-0 flex flex-col border-r border-border bg-sidebar transition-[width] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)] overflow-hidden ${
        collapsed ? "w-14" : "w-[13.5rem]"
      }`}
    >
      {/* Agent card — click to collapse/expand */}
      <button
        onClick={toggleCollapsed}
        title={
          collapsed ? `Expand (Ctrl+/)` : `${agentLabel} — click to collapse`
        }
        className="relative z-10 border-b border-border flex-shrink-0 cursor-pointer w-full text-left"
      >
        <div
          className={`relative overflow-hidden bg-card/60 ${
            collapsed ? "aspect-square w-full" : "aspect-[1.08] w-full"
          }`}
        >
          <img
            src={avatarUrl}
            alt={agentLabel}
            className="h-full w-full object-cover"
          />
        </div>
      </button>

      {/* Navigation */}
      <nav className="relative z-10 flex-1 overflow-y-auto overflow-x-hidden pb-2">
        <div className="space-y-0.5">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              title={`${item.label} — ${item.description}`}
              className={({ isActive }) =>
                `group relative flex items-center transition-colors duration-150 ${
                  collapsed ? "justify-center h-10" : "gap-2.5 px-3 py-2"
                } ${
                  isActive
                    ? "bg-primary/10 text-foreground"
                    : "text-muted-foreground hover:bg-card/60 hover:text-foreground"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {/* Icon */}
                  <span
                    className={`flex h-7 w-7 flex-shrink-0 items-center justify-center transition-colors duration-150 ${
                      isActive
                        ? "text-primary"
                        : "text-muted-foreground group-hover:text-foreground"
                    }`}
                  >
                    <item.Icon size="sm" />
                  </span>

                  {/* Label */}
                  {!collapsed && (
                    <span className="font-mono text-[9px] tracking-[0.22em] leading-none text-current">
                      {item.label}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </div>
      </nav>

      {/* Footer */}
      <div className="relative z-10 border-t border-border flex-shrink-0 px-1.5 py-2">
        {/* Theme toggle */}
        <button
          onClick={() => setTheme(toggleTheme())}
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
          className={`group w-full flex items-center border border-transparent transition-all duration-150 text-muted-foreground hover:border-border hover:bg-card/60 hover:text-foreground ${
            collapsed ? "justify-center h-9" : "gap-2.5 px-2 py-2"
          }`}
        >
          <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center border border-border bg-card/50 text-[12px] transition-all duration-150 group-hover:bg-card">
            {theme === "dark" ? "\u2600" : "\u263E"}
          </span>
          {!collapsed && (
            <span className="font-mono text-[9px] tracking-[0.22em] leading-none text-current">
              {theme === "dark" ? "LIGHT" : "DARK"}
            </span>
          )}
        </button>

        {/* User menu */}
        <div className="relative mt-0.5">
          <button
            onClick={() => setShowUser((current) => !current)}
            className={`group w-full flex items-center border border-transparent transition-all duration-150 text-muted-foreground hover:border-border hover:bg-card/60 hover:text-foreground ${
              collapsed ? "justify-center h-9" : "gap-2.5 px-2 py-2"
            }`}
          >
            <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center border border-border bg-card/50 font-mono text-[9px] font-bold uppercase transition-all duration-150 group-hover:border-primary/50 group-hover:text-primary">
              {user?.name?.charAt(0) || "?"}
            </span>
            {!collapsed && (
              <span className="font-mono text-[9px] tracking-[0.22em] leading-none text-current truncate">
                {user?.name?.toUpperCase() || "USER"}
              </span>
            )}
          </button>

          {showUser && (
            <div
              className={`absolute bottom-full mb-1.5 border border-border bg-sidebar shadow-lg z-50 ${
                collapsed ? "left-full ml-2 min-w-[148px]" : "left-0 right-0"
              }`}
              onMouseLeave={() => setShowUser(false)}
            >
              <div className="px-3 py-2 border-b border-border">
                <div className="font-mono text-[7px] text-muted-foreground tracking-[0.28em] uppercase truncate">
                  {user?.name || "Guest"}
                </div>
              </div>
              <button
                onClick={() => {
                  navigate("/profile");
                  setShowUser(false);
                }}
                className="w-full text-left px-3 py-2 font-mono text-[9px] text-muted-foreground hover:text-foreground hover:bg-primary/10 tracking-[0.2em] transition-colors border-b border-border/50"
              >
                PROFILE
              </button>
              <button
                onClick={() => {
                  setShowUser(false);
                  void logout().then(() => navigate("/login"));
                }}
                className="w-full text-left px-3 py-2 font-mono text-[9px] text-muted-foreground hover:text-destructive hover:bg-destructive/10 tracking-[0.2em] transition-colors"
              >
                LOG OUT
              </button>
            </div>
          )}
        </div>
      </div>
    </aside>
  );
}
