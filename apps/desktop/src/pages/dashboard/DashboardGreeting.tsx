import type { Greeting } from "../../lib/api";

interface DashboardGreetingProps {
  userName?: string;
  tod: string;
  briefLoading: boolean;
  brief: Greeting | null;
}

export function DashboardGreeting({
  userName,
  tod,
  briefLoading,
  brief,
}: DashboardGreetingProps) {
  return (
    <div className="relative text-center space-y-4 pt-4 pb-2">
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[300px] h-[300px] bg-primary/[0.03] rounded-full blur-3xl pointer-events-none" />
      <div className="relative space-y-3">
        <p className="text-xs text-text-muted/40 uppercase tracking-[0.3em]">
          Good {tod}
          {userName ? `, ${userName.split(" ")[0]}` : ""}
        </p>
        {briefLoading && (
          <div className="flex justify-center gap-1 py-2">
            <span className="w-1 h-1 rounded-full bg-text-muted/30 animate-pulse" />
            <span className="w-1 h-1 rounded-full bg-text-muted/30 animate-pulse [animation-delay:150ms]" />
            <span className="w-1 h-1 rounded-full bg-text-muted/30 animate-pulse [animation-delay:300ms]" />
          </div>
        )}
        {brief && !briefLoading && (
          <p className="text-[15px] text-text/90 leading-relaxed max-w-md mx-auto">
            {brief.message}
          </p>
        )}
        {!brief && !briefLoading && (
          <p className="text-[15px] text-text-muted/60">
            What's on your mind?
          </p>
        )}
      </div>
    </div>
  );
}
