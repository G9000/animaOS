import { useState, useEffect, useCallback, useRef } from "react";
import { NavLink, useNavigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { getDbViewerEnabled } from "../pages/settings/AdvancedSettings";
import { getTheme, toggleTheme, type Theme } from "../lib/theme";

const STATIC_NAV_ITEMS = [
  { to: "/", label: "HOME", icon: "\u2302" },
  { to: "/tasks", label: "TASKS", icon: "\u2610" },
  { to: "/chat", label: "CHAT", icon: "\u25B9" },
  { to: "/memory", label: "MEM", icon: "\u25C7" },
  { to: "/graph", label: "GRAPH", icon: "\u25C9" },
  { to: "/soul", label: "DIR", icon: "\u2261" },
  { to: "/consciousness", label: "MIND", icon: "\u25CE" },
  { to: "/mods", label: "MODS", icon: "\u2726" },
  { to: "/settings", label: "CFG", icon: "\u2699" },
];

const DOCK_HIDE_DELAY = 1200;
const DOCK_STORAGE_KEY = "anima-dock-pinned";

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [dbEnabled, setDbEnabled] = useState(getDbViewerEnabled);
  const [dockVisible, setDockVisible] = useState(true);
  const [pinned, setPinned] = useState(() => {
    try {
      return localStorage.getItem(DOCK_STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });
  const [showUser, setShowUser] = useState(false);
  const [theme, setTheme] = useState<Theme>(getTheme);
  const hideTimer = useRef<ReturnType<typeof setTimeout>>(null);
  const dockRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLDivElement>(null);

  const syncSetting = useCallback(() => {
    setDbEnabled(getDbViewerEnabled());
  }, []);

  useEffect(() => {
    window.addEventListener("anima-settings-changed", syncSetting);
    return () =>
      window.removeEventListener("anima-settings-changed", syncSetting);
  }, [syncSetting]);

  // Keyboard toggle: Ctrl+/ or Cmd+/
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "/") {
        e.preventDefault();
        setPinned((p) => {
          const next = !p;
          localStorage.setItem(DOCK_STORAGE_KEY, String(next));
          if (next) setDockVisible(true);
          return next;
        });
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const showDock = useCallback(() => {
    if (hideTimer.current) clearTimeout(hideTimer.current);
    setDockVisible(true);
  }, []);

  const scheduleDockHide = useCallback(() => {
    if (pinned) return;
    if (hideTimer.current) clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(
      () => setDockVisible(false),
      DOCK_HIDE_DELAY,
    );
  }, [pinned]);

  // Auto-hide on route change (unless pinned)
  useEffect(() => {
    if (!pinned) {
      showDock();
      scheduleDockHide();
    }
  }, [location.pathname]);

  const navItems = dbEnabled
    ? [...STATIC_NAV_ITEMS, { to: "/database", label: "DB", icon: "\u25A4" }]
    : STATIC_NAV_ITEMS;

  return (
    <div className="relative h-screen bg-bg text-text overflow-hidden">
      <main className="h-full overflow-hidden">{children}</main>

      {/* Bottom trigger zone — invisible, activates dock on hover */}
      <div
        ref={triggerRef}
        onMouseEnter={showDock}
        className="fixed bottom-0 left-0 right-0 h-4 z-40"
      />

      {/* Dock */}
      <div
        ref={dockRef}
        onMouseEnter={showDock}
        onMouseLeave={scheduleDockHide}
        className={`fixed bottom-3 left-1/2 -translate-x-1/2 z-50 transition-all duration-300 ease-[cubic-bezier(0.16,1,0.3,1)] ${
          dockVisible
            ? "translate-y-0 opacity-100"
            : "translate-y-[calc(100%+20px)] opacity-0 pointer-events-none"
        }`}
      >
        <div className="flex items-center gap-px bg-bg-card/90 backdrop-blur-md border border-border p-1">
          {/* Status dot */}
          <div className="px-2 flex items-center">
            <div className="w-1 h-1 bg-success" />
          </div>

          <div className="w-px h-5 bg-border" />

          {/* Nav items */}
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `group relative flex flex-col items-center justify-center w-10 h-9 font-mono transition-all duration-100 ${
                  isActive
                    ? "text-primary bg-primary/[0.08]"
                    : "text-text-muted/50 hover:text-text hover:bg-bg-input/50"
                }`
              }
            >
              {({ isActive }) => (
                <>
                  <span className="text-[13px] leading-none">{item.icon}</span>
                  <span className="text-[7px] tracking-wider mt-0.5 leading-none">
                    {item.label}
                  </span>
                  {/* Active indicator bar */}
                  {isActive && (
                    <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-4 h-px bg-primary" />
                  )}
                  {/* Tooltip on hover (for items with short labels) */}
                </>
              )}
            </NavLink>
          ))}

          <div className="w-px h-5 bg-border" />

          {/* User section */}
          <div className="relative">
            <button
              onClick={() => setShowUser((v) => !v)}
              className="flex items-center justify-center w-9 h-9 font-mono text-[9px] text-text-muted/50 hover:text-text transition-colors"
            >
              <span className="w-5 h-5 bg-bg-input border border-border flex items-center justify-center text-[8px] uppercase">
                {user?.name?.charAt(0) || "?"}
              </span>
            </button>

            {/* User dropdown */}
            {showUser && (
              <div
                className="absolute bottom-full right-0 mb-2 bg-bg-card border border-border p-1 min-w-[120px]"
                onMouseLeave={() => setShowUser(false)}
              >
                <button
                  onClick={() => {
                    navigate("/profile");
                    setShowUser(false);
                  }}
                  className="w-full text-left px-3 py-1.5 font-mono text-[9px] text-text-muted hover:text-text hover:bg-bg-input/50 tracking-wider transition-colors"
                >
                  PROFILE
                </button>
                <button
                  onClick={() => {
                    setShowUser(false);
                    void logout().then(() => navigate("/login"));
                  }}
                  className="w-full text-left px-3 py-1.5 font-mono text-[9px] text-text-muted/40 hover:text-danger tracking-wider transition-colors"
                >
                  LOGOUT
                </button>
              </div>
            )}
          </div>

          <div className="w-px h-5 bg-border" />

          {/* Theme toggle */}
          <button
            onClick={() => setTheme(toggleTheme())}
            title={
              theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
            }
            className="flex items-center justify-center w-7 h-9 font-mono text-[11px] text-text-muted/50 hover:text-text-muted transition-colors"
          >
            {theme === "dark" ? "\u2600" : "\u263E"}
          </button>

          {/* Pin toggle */}
          <button
            onClick={() => {
              setPinned((p) => {
                const next = !p;
                localStorage.setItem(DOCK_STORAGE_KEY, String(next));
                if (next) setDockVisible(true);
                return next;
              });
            }}
            title={pinned ? "Unpin dock (Ctrl+/)" : "Pin dock (Ctrl+/)"}
            className={`flex items-center justify-center w-7 h-9 font-mono text-[10px] transition-colors ${
              pinned
                ? "text-primary/60 hover:text-primary"
                : "text-text-muted/20 hover:text-text-muted/50"
            }`}
          >
            {pinned ? "\u25A0" : "\u25A1"}
          </button>
        </div>
      </div>
    </div>
  );
}
