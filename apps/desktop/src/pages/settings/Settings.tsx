import { NavLink, Outlet } from "react-router-dom";
import { cn, liftX, AiIcon, SecurityIcon, VaultIcon, LanguageIcon, AppearanceIcon, AdvancedIcon } from "@anima/standard-templates";

function DaemonIcon() {
  return <span className="w-4 h-4 inline-flex items-center justify-center text-xs opacity-80">D</span>;
}

function AccessIcon() {
  return <span className="w-4 h-4 inline-flex items-center justify-center text-xs opacity-80">A</span>;
}

const SETTINGS_SECTIONS = [
  { to: "/settings/ai",         label: "AI",         description: "Provider, model, keys, directive.", Icon: AiIcon },
  { to: "/settings/security",   label: "Security",   description: "Master password & session.",        Icon: SecurityIcon },
  { to: "/settings/access",     label: "Core Access", description: "Client and mod folder grants.",     Icon: AccessIcon },
  { to: "/settings/core-transfer", label: "Core Transfer", description: "Encrypted export and restore.", Icon: VaultIcon },
  { to: "/settings/language",   label: "Language",   description: "Translation preferences.",          Icon: LanguageIcon },
  { to: "/settings/appearance", label: "Appearance", description: "Theme & background.",               Icon: AppearanceIcon },
  { to: "/settings/daemon",     label: "Daemon",     description: "Runtime status and controls.",      Icon: DaemonIcon },
  { to: "/settings/advanced",   label: "Advanced",   description: "Debug tools.",                      Icon: AdvancedIcon },
];

export default function Settings() {
  return (
    <div className="flex h-full overflow-hidden">
      {/* Left rail */}
      <div className="relative z-10 w-48 flex-shrink-0 border-r border-hairline flex flex-col bg-background/25 backdrop-blur-[40px] shadow-[4px_0_24px_rgba(0,0,0,0.12)] pt-hud overflow-visible">
        <div className="px-4 pt-5 pb-3 flex-shrink-0">
          <p className="font-mono text-micro tracking-caps-5 uppercase text-foreground/20 select-none">
            Config
          </p>
        </div>

        <div className="h-px bg-foreground/[0.06] mx-3 flex-shrink-0" />

        <nav className="flex-1 px-2 py-2 space-y-px overflow-visible">
          {SETTINGS_SECTIONS.map(({ to, label, description, Icon }) => (
            <NavLink key={to} to={to} end className={cn(liftX, "group block")}>
              {({ isActive }) => (
                <div
                  className={cn(
                    "relative flex items-center gap-2.5 px-3 py-2.5 transition-colors duration-150",
                    isActive ? "bg-accent z-20" : "group-hover:bg-foreground/[0.06] group-hover:z-10",
                  )}
                >
                  <span className={cn(
                    "shrink-0 transition-colors",
                    isActive ? "text-background" : "text-foreground/40 group-hover:text-foreground/65",
                  )}>
                    <Icon />
                  </span>
                  <div className="flex flex-col min-w-0">
                    <span className={cn(
                      "font-mono text-detail tracking-caps-2 uppercase transition-colors",
                      isActive ? "text-background font-semibold" : "text-foreground/65",
                    )}>
                      {label}
                    </span>
                    <span className={cn(
                      "font-mono text-label mt-0.5 leading-snug tracking-wide truncate",
                      isActive ? "text-background/75" : "text-foreground/40",
                    )}>
                      {description}
                    </span>
                  </div>
                </div>
              )}
            </NavLink>
          ))}
        </nav>
      </div>

      {/* Content pane */}
      <div className="flex-1 overflow-y-auto pt-hud-gap pb-10 px-8">
        <Outlet />
      </div>
    </div>
  );
}
