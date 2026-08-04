import { useState } from "react";
import { NavLink } from "react-router-dom";
import { cn, lift } from "@anima/standard-templates";
import { TOP_NAV_ITEMS } from "../../components/layout/nav-items";
import { glass, CLIP_PATH } from "./hud.styles";

export function TopNav() {
  const [collapsed] = useState(() => {
    try { return localStorage.getItem("anima_nav_collapsed") === "true"; }
    catch { return false; }
  });

  if (collapsed) return null;

  return (
    <div className="relative flex flex-col items-end w-fit self-start pointer-events-auto">
      <div
        style={{ clipPath: CLIP_PATH.cutBottomLeft }}
        className={cn(glass, "p-0 overflow-visible")}
      >
        {TOP_NAV_ITEMS.map(({ to, label, Icon, description }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            title={`${label} — ${description}`}
            aria-label={label}
            className={({ isActive }) =>
              cn(
                lift,
                "group/nav relative size-hud grid items-center justify-center gap-2 z-10",
                isActive
                  ? "bg-accent text-accent-foreground"
                  : "text-foreground/30 hover:text-accent hover:bg-accent/20",
              )
            }
          >
            {() => <Icon size="sm" />}
          </NavLink>
        ))}
      </div>
    </div>
  );
}
