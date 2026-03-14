import { NavLink, Outlet } from "react-router-dom";

const SETTINGS_SECTIONS = [
  {
    to: "/settings/ai",
    label: "AI",
    description: "Provider, model, keys, and system prompt.",
  },
  {
    to: "/settings/security",
    label: "Security",
    description: "Master password and session unlock controls.",
  },
  {
    to: "/settings/vault",
    label: "Vault",
    description: "Encrypted backup export and import.",
  },
  {
    to: "/settings/advanced",
    label: "Advanced",
    description: "Debug tools and developer options.",
  },
];

export default function Settings() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl mx-auto px-8 py-8 space-y-8">
        <header className="space-y-2">
          <h1 className="text-sm tracking-widest uppercase">Settings</h1>
          <p className="text-sm text-(--color-text-muted)">
            Runtime configuration, vault operations, and authentication controls live
            in separate sections now.
          </p>
        </header>

        <nav className="grid gap-2 md:grid-cols-4">
          {SETTINGS_SECTIONS.map((section) => (
            <NavLink
              key={section.to}
              to={section.to}
              end
              className={({ isActive }) =>
                `rounded-sm border p-4 transition-colors ${
                  isActive
                    ? "border-(--color-primary) bg-(--color-bg-card) text-(--color-text)"
                    : "border-(--color-border) text-(--color-text-muted) hover:border-(--color-text-muted) hover:text-(--color-text)"
                }`
              }
            >
              <div className="text-[11px] uppercase tracking-[0.2em]">{section.label}</div>
              <div className="mt-2 text-xs leading-5">{section.description}</div>
            </NavLink>
          ))}
        </nav>

        <Outlet />
      </div>
    </div>
  );
}
