import { useEffect, useRef, useState } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { cn, lift, ResetIcon } from "@anima/standard-templates";
import { TOP_NAV_ITEMS } from "../../components/layout/nav-items";
import { useLayoutActions } from "../../context/LayoutActionsContext";
import { CLIP_PATH } from "./hud.styles";

export function NavMenu() {
  const navigate = useNavigate();
  const location = useLocation();
  const { dashboardReset } = useLayoutActions();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const activeNavItem = TOP_NAV_ITEMS.find((item) =>
    item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to),
  );

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  if (!activeNavItem) return null;

  return (
    <div ref={ref} className="fixed bottom-0 right-0 z-30 w-42 pointer-events-auto">
      {open && (
        <div
          style={{ clipPath: CLIP_PATH.cutTopLeft, width: "calc(100% - 12px)" }}
          className="absolute bottom-full right-0 bg-background/95 backdrop-blur-[44px] border border-hairline shadow-[0_8px_32px_rgba(0,0,0,0.20)] flex flex-col"
        >
          {TOP_NAV_ITEMS.map(({ to, Icon, description }) => {
            const isActive = to === "/" ? location.pathname === "/" : location.pathname.startsWith(to);
            return (
              <button
                key={to}
                onClick={() => { navigate(to); setOpen(false); }}
                className={cn(
                  "w-full flex items-center gap-3 px-4 py-1 font-mono text-ui uppercase transition-colors",
                  isActive
                    ? "bg-accent text-accent-foreground"
                    : "text-foreground/40 hover:text-foreground hover:bg-foreground/[0.04]",
                )}
              >
                <Icon size="sm" />
                {description}
              </button>
            );
          })}

          {dashboardReset && (
            <>
              <div className="mx-4 my-1 border-t border-hairline" />
              <button
                onClick={() => { dashboardReset(); setOpen(false); }}
                className="group/reset w-full flex items-center gap-3 px-4 py-1 font-mono text-ui uppercase text-foreground/40 hover:text-foreground hover:bg-foreground/[0.04] transition-colors"
              >
                <ResetIcon size="sm" className="transition-transform duration-500 group-hover/reset:-rotate-[360deg] shrink-0" />
                Reset
              </button>
            </>
          )}
        </div>
      )}

      <div className={lift}>
        <button
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-haspopup="menu"
          style={{ clipPath: CLIP_PATH.cutTopLeft }}
          className="w-full bg-accent px-4 py-2 flex items-center justify-between"
        >
          <span className="font-mono text-title uppercase text-accent-foreground leading-none">
            {activeNavItem.description}
          </span>
        </button>
      </div>
    </div>
  );
}
