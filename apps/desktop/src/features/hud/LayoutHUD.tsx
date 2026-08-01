import { AgentCard } from "./AgentCard";
import { TopNav } from "./TopNav";
import { NavMenu } from "./NavMenu";
import { InboxPanel } from "./InboxPanel";
import { useCoreFSReadiness } from "../../context/CoreFSReadinessContext";

export function LayoutHUD() {
  const { catalogReady } = useCoreFSReadiness();
  if (!catalogReady) return null;

  return (
    <div className="relative w-full h-screen flex justify-between items-stretch pointer-events-none">
      <div
        className="absolute bottom-0 left-0 right-0 h-px pointer-events-none"
        style={{ background: "linear-gradient(to right, var(--color-accent) 0%, transparent 40%, transparent 60%, var(--color-accent) 100%)" }}
      />
      <AgentCard />
      <TopNav />
      <NavMenu />
      <InboxPanel />
    </div>
  );
}
