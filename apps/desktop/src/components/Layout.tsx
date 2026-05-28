import type { ReactNode } from "react";
import { LayoutTopNav } from "./layout/LayoutTopNav";

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="relative h-screen bg-background text-foreground overflow-hidden">
      {/* Nav floats above everything */}
      <div className="absolute top-3 left-3 right-3 z-30">
        <LayoutTopNav />
      </div>
      {/* Content fills full height — banner goes behind the nav */}
      <main className="h-full overflow-hidden min-w-0">{children}</main>
    </div>
  );
}
