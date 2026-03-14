import { useState, useEffect, useCallback } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { getDbViewerEnabled } from "../pages/settings/AdvancedSettings";

const STATIC_NAV_ITEMS = [
  { to: "/", label: "Home" },
  { to: "/tasks", label: "Tasks" },
  { to: "/chat", label: "Chat" },
  { to: "/memory", label: "Memory" },
  { to: "/soul", label: "Soul" },
  { to: "/consciousness", label: "Mind" },
  { to: "/settings", label: "Settings" },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [dbEnabled, setDbEnabled] = useState(getDbViewerEnabled);

  const syncSetting = useCallback(() => {
    setDbEnabled(getDbViewerEnabled());
  }, []);

  useEffect(() => {
    window.addEventListener("anima-settings-changed", syncSetting);
    return () =>
      window.removeEventListener("anima-settings-changed", syncSetting);
  }, [syncSetting]);

  const navItems = dbEnabled
    ? [...STATIC_NAV_ITEMS, { to: "/database", label: "DB" }]
    : STATIC_NAV_ITEMS;

  return (
    <div className="flex h-screen bg-(--color-bg) text-(--color-text)">
      {/* Sidebar */}
      <aside className="w-[140px] border-r border-(--color-border) flex flex-col shrink-0">
        {/* Logo */}
        <div className="px-4 py-5">
          <span className="font-mono text-xs font-semibold tracking-[0.25em] uppercase text-(--color-text)/70">
            ANIMA
          </span>
        </div>

        {/* Nav */}
        <nav className="flex-1 flex flex-col gap-0.5 px-2">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `relative px-3 py-2 rounded-md text-[13px] transition-all duration-150 ${
                  isActive
                    ? "bg-(--color-bg-card) text-(--color-text) font-medium before:absolute before:left-0 before:top-1/2 before:-translate-y-1/2 before:w-[3px] before:h-4 before:rounded-r-full before:bg-(--color-primary)"
                    : "text-(--color-text-muted) hover:text-(--color-text) hover:bg-(--color-bg-card)/50"
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        {/* Bottom */}
        <div className="px-2 py-4 border-t border-(--color-border) space-y-1">
          <button
            onClick={() => navigate("/profile")}
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-md text-[13px] text-(--color-text-muted) hover:text-(--color-text) hover:bg-(--color-bg-card)/50 transition-colors"
          >
            <span className="w-5 h-5 rounded-full bg-(--color-bg-input) border border-(--color-border) flex items-center justify-center text-[9px] uppercase shrink-0">
              {user?.name?.charAt(0) || "?"}
            </span>
            <span className="truncate">{user?.name}</span>
          </button>
          <button
            onClick={() => {
              void logout().then(() => navigate("/login"));
            }}
            className="w-full px-3 py-1.5 rounded-md text-[12px] text-(--color-text-muted)/50 hover:text-(--color-danger) text-left transition-colors"
          >
            Log out
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">{children}</main>
    </div>
  );
}
