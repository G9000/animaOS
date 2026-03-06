import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

const NAV_ITEMS = [
  { to: "/", label: "Home" },
  { to: "/chat", label: "Chat" },
  { to: "/memory", label: "Memory" },
  { to: "/soul", label: "Soul" },
  { to: "/settings", label: "Config" },
];

export default function Layout({ children }: { children: React.ReactNode }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

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
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) =>
                `px-3 py-2 rounded-sm text-[13px] transition-colors ${
                  isActive
                    ? "bg-(--color-bg-card) text-(--color-text)"
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
            className="w-full flex items-center gap-2.5 px-3 py-2 rounded-sm text-[13px] text-(--color-text-muted) hover:text-(--color-text) hover:bg-(--color-bg-card)/50 transition-colors"
          >
            <span className="w-5 h-5 rounded-full bg-(--color-bg-input) border border-(--color-border) flex items-center justify-center text-[9px] uppercase shrink-0">
              {user?.name?.charAt(0) || "?"}
            </span>
            <span className="truncate">{user?.name}</span>
          </button>
          <button
            onClick={logout}
            className="w-full px-3 py-1.5 rounded-sm text-[12px] text-(--color-text-muted)/50 hover:text-(--color-danger) text-left transition-colors"
          >
            Log out
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">
        {children}
      </main>
    </div>
  );
}
