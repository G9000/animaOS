import { useState, useEffect, useCallback } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { getDbViewerEnabled } from "../pages/settings/AdvancedSettings";
import { getTheme, toggleTheme, type Theme } from "../lib/theme";
import { api } from "../lib/api";
import { getUnlockToken } from "../lib/api";
import { API_BASE } from "../lib/runtime";
import personaAvatar from "../assets/persona-default.svg";

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

const SIDEBAR_STORAGE_KEY = "anima-sidebar-collapsed";

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [dbEnabled, setDbEnabled] = useState(getDbViewerEnabled);
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
    } catch {
      return false;
    }
  });
  const [showUser, setShowUser] = useState(false);
  const [theme, setTheme] = useState<Theme>(getTheme);
  const [agentAvatarUrl, setAgentAvatarUrl] = useState<string>(personaAvatar);
  const [agentName, setAgentName] = useState("ANIMA");

  useEffect(() => {
    if (user?.id == null) return;
    let revoked = false;
    api.consciousness
      .getAgentProfile(user.id)
      .then(async (profile) => {
        if (profile.agentName) setAgentName(profile.agentName.toUpperCase());
        if (!profile.avatarUrl) return;
        const token = getUnlockToken();
        const headers: Record<string, string> = {};
        if (token) headers["x-anima-unlock"] = token;
        const res = await fetch(`${API_BASE}${profile.avatarUrl}`, { headers });
        if (!res.ok || revoked) return;
        const blob = await res.blob();
        if (!revoked) setAgentAvatarUrl(URL.createObjectURL(blob));
      })
      .catch(() => {});
    return () => {
      revoked = true;
    };
  }, [user?.id]);

  useEffect(() => {
    const refresh = () => {
      if (user?.id == null) return;
      let revoked = false;
      api.consciousness
        .getAgentProfile(user.id)
        .then(async (profile) => {
          if (profile.agentName) setAgentName(profile.agentName.toUpperCase());
          if (!profile.avatarUrl) {
            setAgentAvatarUrl(personaAvatar);
            return;
          }
          const token = getUnlockToken();
          const headers: Record<string, string> = {};
          if (token) headers["x-anima-unlock"] = token;
          const res = await fetch(`${API_BASE}${profile.avatarUrl}`, {
            headers,
          });
          if (!res.ok || revoked) return;
          const blob = await res.blob();
          if (!revoked) setAgentAvatarUrl(URL.createObjectURL(blob));
        })
        .catch(() => {});
      return () => {
        revoked = true;
      };
    };
    window.addEventListener("anima-avatar-changed", refresh);
    return () => window.removeEventListener("anima-avatar-changed", refresh);
  }, [user?.id]);

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
        setCollapsed((c) => {
          const next = !c;
          localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
          return next;
        });
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const navItems = dbEnabled
    ? [...STATIC_NAV_ITEMS, { to: "/database", label: "DB", icon: "\u25A4" }]
    : STATIC_NAV_ITEMS;

  return (
    <div className="flex h-screen bg-background text-foreground overflow-hidden">
      {/* Sidebar */}
      <aside
        className={`flex-shrink-0 flex flex-col border-r border-border bg-sidebar transition-[width] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)] overflow-hidden ${
          collapsed ? "w-12" : "w-36"
        }`}
      >
        {/* Header: logo + toggle */}
        <div
          className={`flex items-center border-b border-border h-10 flex-shrink-0 ${collapsed ? "justify-center" : "justify-between px-3"}`}
        >
          {!collapsed && (
            <span className="font-mono text-[9px] text-primary tracking-[0.35em]">
              ANIMA
            </span>
          )}
          <button
            onClick={() => {
              setCollapsed((c) => {
                const next = !c;
                localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
                return next;
              });
            }}
            title={collapsed ? "Expand (Ctrl+/)" : "Collapse (Ctrl+/)"}
            className="w-8 h-8 flex items-center justify-center text-muted-foreground/40 hover:text-foreground transition-colors"
          >
            <span className="font-mono text-[11px]">
              {collapsed ? "\u25B8" : "\u25C2"}
            </span>
          </button>
        </div>

        {/* Agent avatar — click to open agent config */}
        <NavLink
          to="/agent"
          title={agentName}
          className={({ isActive }) =>
            `flex flex-col items-center py-4 border-b border-border flex-shrink-0 transition-colors hover:bg-accent/40 cursor-pointer ${
              collapsed ? "gap-0" : "gap-1.5"
            } ${isActive ? "bg-accent/30" : ""}`
          }
        >
          <div className="relative">
            <img
              src={agentAvatarUrl}
              alt={agentName}
              className={`rounded-full border border-border object-cover transition-all duration-200 ${collapsed ? "w-7 h-7" : "w-12 h-12"}`}
            />
            <span className="absolute bottom-0 right-0 w-2 h-2 rounded-full bg-success border-2 border-sidebar" />
          </div>
          {!collapsed && (
            <span className="font-mono text-[9px] text-muted-foreground/60 tracking-widest">
              {agentName}
            </span>
          )}
        </NavLink>

        {/* Nav */}
        <nav className="flex-1 flex flex-col gap-px py-1 overflow-y-auto overflow-x-hidden">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              title={collapsed ? item.label : undefined}
              className={({ isActive }) =>
                `flex items-center gap-3 font-mono transition-all duration-100 border-l-2 mx-1 px-2 py-2 ${
                  collapsed ? "justify-center mx-0 px-0" : ""
                } ${
                  isActive
                    ? "text-primary bg-primary/[0.08] border-primary"
                    : "text-muted-foreground/60 hover:text-foreground hover:bg-input/40 border-transparent"
                }`
              }
            >
              <span className="text-[14px] leading-none flex-shrink-0">
                {item.icon}
              </span>
              {!collapsed && (
                <span className="text-[9px] tracking-wider">{item.label}</span>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Footer: theme + user */}
        <div className="border-t border-border flex-shrink-0">
          <button
            onClick={() => setTheme(toggleTheme())}
            title={theme === "dark" ? "Light mode" : "Dark mode"}
            className={`w-full flex items-center gap-3 px-3 py-2.5 font-mono text-muted-foreground/50 hover:text-foreground transition-colors ${collapsed ? "justify-center px-0" : ""}`}
          >
            <span className="text-[13px] flex-shrink-0">
              {theme === "dark" ? "\u2600" : "\u263E"}
            </span>
            {!collapsed && (
              <span className="text-[9px] tracking-wider">
                {theme === "dark" ? "LIGHT" : "DARK"}
              </span>
            )}
          </button>

          <div className="relative">
            <button
              onClick={() => setShowUser((v) => !v)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 font-mono text-muted-foreground/50 hover:text-foreground transition-colors ${collapsed ? "justify-center px-0" : ""}`}
            >
              <span className="w-5 h-5 bg-input border border-border flex items-center justify-center text-[8px] uppercase flex-shrink-0">
                {user?.name?.charAt(0) || "?"}
              </span>
              {!collapsed && (
                <span className="text-[9px] tracking-wider truncate">
                  {user?.name || "USER"}
                </span>
              )}
            </button>

            {showUser && (
              <div
                className="absolute bottom-full left-0 mb-1 bg-card border border-border p-1 min-w-[128px] z-50"
                onMouseLeave={() => setShowUser(false)}
              >
                <button
                  onClick={() => {
                    navigate("/profile");
                    setShowUser(false);
                  }}
                  className="w-full text-left px-3 py-1.5 font-mono text-[9px] text-muted-foreground hover:text-foreground hover:bg-input/50 tracking-wider transition-colors"
                >
                  PROFILE
                </button>
                <button
                  onClick={() => {
                    setShowUser(false);
                    void logout().then(() => navigate("/login"));
                  }}
                  className="w-full text-left px-3 py-1.5 font-mono text-[9px] text-muted-foreground/40 hover:text-destructive tracking-wider transition-colors"
                >
                  LOGOUT
                </button>
              </div>
            )}
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 h-full overflow-hidden min-w-0">{children}</main>
    </div>
  );
}
