import type { ReactNode } from "react";
import { LayoutHUD } from "../features/hud";
import BackgroundLayer from "./BackgroundLayer";
import InitiativeOverlay from "./InitiativeOverlay";
import { LayoutActionsProvider } from "../context/LayoutActionsContext";

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <LayoutActionsProvider>
      <div className="relative h-screen text-foreground overflow-hidden">
        <BackgroundLayer />
        {/* Nav floats above everything */}
        <div className="absolute z-30 w-full pointer-events-none">
          <LayoutHUD />
        </div>
        {/* Content fills full height */}
        <main className="h-full overflow-hidden min-w-0">{children}</main>
        <InitiativeOverlay />
      </div>
    </LayoutActionsProvider>
  );
}
