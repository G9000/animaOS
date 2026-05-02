import type { ReactNode } from "react";
import { LayoutSidebar } from "./layout/LayoutSidebar";

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-screen bg-background text-foreground overflow-hidden">
      <LayoutSidebar />
      <main className="flex-1 h-full overflow-hidden min-w-0 bg-background">{children}</main>
    </div>
  );
}
