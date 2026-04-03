import { useCallback, useEffect, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { useAgentProfile } from "../../hooks/useAgentProfile";
import { SETTINGS_CHANGED_EVENT } from "../../lib/events";
import { getDbViewerEnabled } from "../../lib/preferences";
import { getTheme, toggleTheme, type Theme } from "../../lib/theme";

interface NavItem {
  to: string;
  label: string;
  icon: string;
  description: string;
}

const STATIC_NAV_ITEMS: NavItem[] = [
  { to: "/", label: "HOME", icon: "\u2302", description: "dashboard" },
  { to: "/tasks", label: "TASKS", icon: "\u2610", description: "queue" },
  { to: "/chat", label: "CHAT", icon: "\u25B9", description: "console" },
  { to: "/memory", label: "MEM", icon: "\u25C7", description: "archive" },
  {
    to: "/consciousness",
    label: "MIND",
    icon: "\u25EF",
    description: "consciousness",
  },
  { to: "/mods", label: "MODS", icon: "\u2726", description: "extensions" },
  { to: "/settings", label: "CFG", icon: "\u2699", description: "system" },
];

const DATABASE_NAV_ITEM: NavItem = {
  to: "/database",
  label: "DB",
  icon: "\u25A4",
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
      {/* Ambient layer */}
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute inset-x-0 top-0 h-40 bg-gradient-to-b from-primary/10 via-primary/[0.03] to-transparent" />
        <div className="absolute inset-y-0 right-0 w-px bg-gradient-to-b from-primary/25 via-primary/8 to-transparent" />
      </div>

      {/* Header */}
      <div
        className={`relative z-10 flex items-center border-b border-border/60 min-h-[3rem] flex-shrink-0 ${
          collapsed ? "justify-center" : "justify-between px-3 py-2"
        }`}
      >
        {!collapsed && (
          <div className="min-w-0 select-none">
            <div className="flex items-center gap-2">
              <span className="h-1.5 w-1.5 bg-primary shadow-[0_0_10px_rgba(94,160,171,0.8)]" />
              <span className="font-mono text-[9px] font-bold text-primary tracking-[0.42em]">
                {agentLabel}
              </span>
            </div>
            <div className="mt-0.5 font-mono text-[7px] text-muted-foreground/22 tracking-[0.28em] uppercase">
              Cognitive Shell
            </div>
          </div>
        )}
        <button
          onClick={toggleCollapsed}
          title={collapsed ? "Expand (Ctrl+/)" : "Collapse (Ctrl+/)"}
          className="flex h-8 w-8 items-center justify-center border border-transparent text-muted-foreground/30 hover:border-border/80 hover:bg-card/80 hover:text-primary transition-all duration-150"
        >
          <span className="font-mono text-[10px]">
            {collapsed ? "\u25B8" : "\u25C2"}
          </span>
        </button>
      </div>

      {/* Agent card */}
      <NavLink
        to="/agent"
        title={agentLabel}
        className={({ isActive }) =>
          `relative z-10 border-b border-border/60 flex-shrink-0 transition-all duration-200 cursor-pointer ${
            isActive ? "bg-primary/[0.06]" : "hover:bg-card/40"
          }`
        }
      >
        <div className={`p-2 ${collapsed ? "flex justify-center" : ""}`}>
          <div
            className={`relative overflow-hidden border border-border/70 bg-card/60 ${
              collapsed ? "size-10" : "aspect-[1.08] w-full"
            }`}
          >
            <img
              src={avatarUrl}
              alt={agentLabel}
              className="h-full w-full object-cover transition-transform duration-500 ease-[cubic-bezier(0.16,1,0.3,1)] hover:scale-[1.04]"
            />

            {/* CRT scanline overlay */}
            {!collapsed && (
              <div className="absolute inset-0 bg-[repeating-linear-gradient(0deg,transparent,transparent_3px,rgba(0,0,0,0.06)_3px,rgba(0,0,0,0.06)_4px)] pointer-events-none" />
            )}

            {/* Online indicator */}
            <span className="absolute right-1.5 top-1.5 h-2 w-2 border-[1.5px] border-sidebar bg-success shadow-[0_0_8px_rgba(74,222,128,0.65)]" />

            {/* Agent info scrim */}
            {!collapsed && (
              <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-sidebar/98 via-sidebar/75 to-transparent pt-8 pb-3 px-2.5">
                <div className="font-mono text-[7px] text-muted-foreground/40 tracking-[0.25em] uppercase mb-0.5">
                  Active Agent
                </div>
                <div className="font-mono text-[10px] font-bold text-foreground/90 tracking-[0.18em] truncate">
                  {agentLabel}
                </div>
              </div>
            )}
          </div>
        </div>
      </NavLink>

      {/* Navigation */}
      <nav className="relative z-10 flex-1 overflow-y-auto overflow-x-hidden px-1.5 py-2.5">
        {!collapsed && (
          <div className="px-2 pb-2 font-mono text-[7px] text-muted-foreground/22 tracking-[0.32em] uppercase">
            Navigation
          </div>
        )}

        <div className="space-y-0.5">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              title={
                collapsed ? `${item.label} — ${item.description}` : undefined
              }
              className={({ isActive }) =>
                `group relative flex items-center border transition-all duration-150 ${
                  collapsed
                    ? "justify-center h-10 border-transparent"
                    : "gap-2.5 px-2 py-2 border-transparent"
                } ${
                  isActive
                    ? "border-primary/18 bg-primary/[0.07] text-foreground"
                    : "text-muted-foreground/50 hover:border-border/50 hover:bg-card/45 hover:text-foreground/85"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  {/* Left-edge active bar */}
                  {isActive && (
                    <span className="absolute left-0 inset-y-0 w-[2px] bg-primary shadow-[0_0_6px_rgba(94,160,171,0.6)]" />
                  )}

                  {/* Icon */}
                  <span
                    className={`flex h-7 w-7 flex-shrink-0 items-center justify-center border text-[12px] leading-none transition-all duration-150 ${
                      isActive
                        ? "border-primary/30 bg-primary/12 text-primary"
                        : "border-border/45 bg-card/35 text-muted-foreground/45 group-hover:border-border/70 group-hover:bg-card/60 group-hover:text-foreground/75"
                    }`}
                  >
                    {item.icon}
                  </span>

                  {/* Label + description */}
                  {!collapsed && (
                    <span className="min-w-0 flex-1">
                      <span className="block font-mono text-[9px] tracking-[0.22em] leading-none text-current">
                        {item.label}
                      </span>
                      <span className="mt-1 block font-mono text-[7px] text-muted-foreground/28 tracking-[0.14em] lowercase">
                        {item.description}
                      </span>
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </div>
      </nav>

      {/* Footer */}
      <div className="relative z-10 border-t border-border/60 flex-shrink-0 px-1.5 py-2">
        {!collapsed && (
          <div className="px-2 pb-2 font-mono text-[7px] text-muted-foreground/22 tracking-[0.32em] uppercase">
            System
          </div>
        )}

        {/* Theme toggle */}
        <button
          onClick={() => setTheme(toggleTheme())}
          title={theme === "dark" ? "Switch to light" : "Switch to dark"}
          className={`group w-full flex items-center border border-transparent transition-all duration-150 text-muted-foreground/40 hover:border-border/50 hover:bg-card/45 hover:text-foreground/80 ${
            collapsed ? "justify-center h-9" : "gap-2.5 px-2 py-2"
          }`}
        >
          <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center border border-border/45 bg-card/35 text-[12px] transition-all duration-150 group-hover:border-border/70 group-hover:bg-card/60">
            {theme === "dark" ? "\u2600" : "\u263E"}
          </span>
          {!collapsed && (
            <span className="min-w-0 flex-1 text-left">
              <span className="block font-mono text-[9px] tracking-[0.22em] leading-none text-current">
                {theme === "dark" ? "LIGHT" : "DARK"}
              </span>
              <span className="mt-1 block font-mono text-[7px] text-muted-foreground/28 tracking-[0.14em] lowercase">
                interface tone
              </span>
            </span>
          )}
        </button>

        {/* User menu */}
        <div className="relative mt-0.5">
          <button
            onClick={() => setShowUser((current) => !current)}
            className={`group w-full flex items-center border border-transparent transition-all duration-150 text-muted-foreground/40 hover:border-border/50 hover:bg-card/45 hover:text-foreground/80 ${
              collapsed ? "justify-center h-9" : "gap-2.5 px-2 py-2"
            }`}
          >
            <span className="flex h-7 w-7 flex-shrink-0 items-center justify-center border border-border/45 bg-card/35 font-mono text-[9px] font-bold uppercase transition-all duration-150 group-hover:border-primary/30 group-hover:bg-primary/8 group-hover:text-primary">
              {user?.name?.charAt(0) || "?"}
            </span>
            {!collapsed && (
              <span className="min-w-0 flex-1 text-left">
                <span className="block font-mono text-[9px] tracking-[0.22em] leading-none text-current truncate">
                  {user?.name?.toUpperCase() || "USER"}
                </span>
                <span className="mt-1 block font-mono text-[7px] text-muted-foreground/28 tracking-[0.14em] lowercase">
                  session
                </span>
              </span>
            )}
          </button>

          {showUser && (
            <div
              className={`absolute bottom-full mb-1.5 border border-border/70 bg-sidebar shadow-[0_-8px_28px_rgba(0,0,0,0.35)] backdrop-blur-sm z-50 ${
                collapsed ? "left-full ml-2 min-w-[148px]" : "left-0 right-0"
              }`}
              onMouseLeave={() => setShowUser(false)}
            >
              <div className="px-3 py-2 border-b border-border/40">
                <div className="font-mono text-[7px] text-muted-foreground/30 tracking-[0.28em] uppercase truncate">
                  {user?.name || "Guest"}
                </div>
              </div>
              <button
                onClick={() => {
                  navigate("/profile");
                  setShowUser(false);
                }}
                className="w-full text-left px-3 py-2 font-mono text-[9px] text-muted-foreground/65 hover:text-foreground hover:bg-primary/[0.07] tracking-[0.2em] transition-colors border-b border-border/30"
              >
                PROFILE
              </button>
              <button
                onClick={() => {
                  setShowUser(false);
                  void logout().then(() => navigate("/login"));
                }}
                className="w-full text-left px-3 py-2 font-mono text-[9px] text-muted-foreground/35 hover:text-destructive hover:bg-destructive/[0.05] tracking-[0.2em] transition-colors"
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
