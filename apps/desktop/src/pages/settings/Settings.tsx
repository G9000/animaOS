import { NavLink, Outlet } from "react-router-dom";

const SETTINGS_SECTIONS = [
  {
    to: "/settings/ai",
    label: "AI",
    description: "Provider, model, keys, system prompt.",
  },
  {
    to: "/settings/security",
    label: "SECURITY",
    description: "Master password, session unlock.",
  },
  {
    to: "/settings/vault",
    label: "VAULT",
    description: "Encrypted backup export/import.",
  },
  {
    to: "/settings/language",
    label: "LANGUAGE",
    description: "Translation and display preferences.",
  },
  {
    to: "/settings/advanced",
    label: "ADVANCED",
    description: "Debug tools, developer options.",
  },
];

export default function Settings() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8 space-y-8">
        <header className="space-y-2">
          <h1 className="font-mono text-sm tracking-[0.18em] uppercase">CONFIG</h1>
          <p className="font-mono text-[10px] text-muted-foreground/50 tracking-[0.22em] uppercase">
            RUNTIME CONFIGURATION, VAULT, AND AUTH CONTROLS
          </p>
        </header>

        <nav className="grid gap-2 md:grid-cols-5">
          {SETTINGS_SECTIONS.map((section) => (
            <NavLink
              key={section.to}
              to={section.to}
              end
              className={({ isActive }) =>
                `p-4 transition-all rounded-none border ${
                  isActive
                    ? "bg-primary text-primary-foreground border-primary "
                    : "bg-card text-muted-foreground hover:text-foreground border-border hover:bg-secondary hover:"
                }`
              }
            >
              <div className="font-mono text-[10px] tracking-[0.18em] uppercase">
                {section.label}
              </div>
              <div className="mt-2 text-[11px] text-muted-foreground/60 leading-relaxed">
                {section.description}
              </div>
            </NavLink>
          ))}
        </nav>

        <Outlet />
      </div>
    </div>
  );
}
