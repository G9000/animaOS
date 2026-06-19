import type { ReactNode } from "react";
import { LayoutTopNav } from "./layout/LayoutTopNav";
import BackgroundLayer from "./BackgroundLayer";

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="relative h-screen text-foreground overflow-hidden">
      <BackgroundLayer />
      {/* Nav floats above everything */}
      <div className="absolute top-4 left-4 right-4 z-30">
        <LayoutTopNav />
      </div>
      {/* Content fills full height */}
      <main className="h-full overflow-hidden min-w-0">{children}</main>
    </div>
  );
}
