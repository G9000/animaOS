import { useState } from "react";
import { NavLink } from "react-router-dom";
import { cn } from "@anima/standard-templates";
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
        {TOP_NAV_ITEMS.map(({ to, Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            style={({ isActive }) =>
              isActive
                ? {
                    transform: "translate(-2px, -4px)",
                    boxShadow:
                      "2px 4px 0 var(--color-accent-dark), 4px 8px 0 color-mix(in oklch, var(--color-accent-dark) 40%, transparent)",
                  }
                : {}
            }
            className={({ isActive }) =>
              cn(
                "group/nav relative size-16 grid items-center justify-center gap-2 z-10",
                "transition-[transform,box-shadow,filter] duration-150",
                isActive
                  ? "bg-accent text-accent-foreground"
                  : "text-foreground/30 hover:text-accent hover:bg-accent/20",
              )
            }
            onMouseEnter={(e) => {
              const el = e.currentTarget as HTMLElement;
              el.style.transform = "translate(-2px, -4px)";
              el.style.boxShadow =
                "2px 4px 0 var(--color-accent-dark), 4px 8px 0 color-mix(in oklch, var(--color-accent-dark) 40%, transparent)";
            }}
            onMouseLeave={(e) => {
              const el = e.currentTarget as HTMLElement;
              if (el.classList.contains("bg-accent")) return;
              el.style.transform = "";
              el.style.boxShadow = "";
            }}
          >
            {() => <Icon size="sm" />}
          </NavLink>
        ))}
      </div>
    </div>
  );
}
