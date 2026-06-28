import type { ReactNode } from "react";
import { LayoutHUD } from "./layout/LayoutHUD";
import BackgroundLayer from "./BackgroundLayer";

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="relative h-screen text-foreground overflow-hidden">
      <BackgroundLayer />
      {/* Nav floats above everything */}
      <div className="absolute z-30 w-full">
        <LayoutHUD />
      </div>
      {/* Content fills full height */}
      <main className="h-full overflow-hidden min-w-0">{children}</main>
    </div>
  );
}
