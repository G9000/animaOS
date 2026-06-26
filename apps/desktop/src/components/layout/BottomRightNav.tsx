import { useNavigate } from "react-router-dom";
import { ChatIcon, cn } from "@anima/standard-templates";

export function BottomRightNav() {
  const navigate = useNavigate();

  return (
    <nav className="fixed bottom-6 right-6 z-30">
      <button
        onClick={() => navigate("/chat")}
        title="Open chat"
        className={cn(
          "group relative flex size-12 items-center justify-center",
          "bg-background/25 backdrop-blur-[40px]",
          "border border-foreground/[0.08]",
          "shadow-[0_20px_50px_-12px_rgba(0,0,0,0.28)]",
          "text-foreground/70 hover:text-foreground hover:bg-foreground/10",
          "transition-colors duration-150",
        )}
      >
        <ChatIcon size="md" />
        <span
          className={cn(
            "pointer-events-none absolute right-full top-1/2 z-50 mr-2 -translate-y-1/2",
            "rounded border border-border bg-background px-1.5 py-0.5",
            "text-[10px] font-medium text-foreground shadow-sm",
            "opacity-0 transition-opacity group-hover:opacity-100",
            "whitespace-nowrap",
          )}
        >
          Open chat
        </span>
      </button>
    </nav>
  );
}
