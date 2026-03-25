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
    to: "/settings/advanced",
    label: "ADVANCED",
    description: "Debug tools, developer options.",
  },
];

export default function Settings() {
  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl mx-auto px-8 py-8 space-y-8">
        <header className="space-y-2">
          <h1 className="font-mono text-sm tracking-wider">CONFIG</h1>
          <p className="font-mono text-[10px] text-muted-foreground/40 tracking-wider">
            RUNTIME CONFIGURATION, VAULT, AND AUTH CONTROLS
          </p>
        </header>

        <nav className="grid gap-px md:grid-cols-4 bg-border">
          {SETTINGS_SECTIONS.map((section) => (
            <NavLink
              key={section.to}
              to={section.to}
              end
              className={({ isActive }) =>
                `p-4 transition-colors ${
                  isActive
                    ? "bg-primary/[0.06] border-l-2 border-primary text-foreground"
                    : "bg-card text-muted-foreground hover:text-foreground border-l-2 border-transparent"
                }`
              }
            >
              <div className="font-mono text-[10px] tracking-wider">{section.label}</div>
              <div className="mt-1.5 text-[11px] text-muted-foreground/50 leading-relaxed">{section.description}</div>
            </NavLink>
          ))}
        </nav>

        <Outlet />
      </div>
    </div>
  );
}
