import type { Greeting } from "@anima/api-client";
import { useAnimaSymbol } from "@anima/standard-templates";

interface DashboardGreetingProps {
  userName?: string;
  tod: string;
  briefLoading: boolean;
  brief: Greeting | null;
}

function getSymbolSpeed(briefLoading: boolean): number {
  return briefLoading ? 2 : 0.6;
}

export function DashboardGreeting({
  userName,
  tod,
  briefLoading,
  brief,
}: DashboardGreetingProps) {
  const symbol = useAnimaSymbol(getSymbolSpeed(briefLoading));
  const firstName = userName?.split(" ")[0];

  const fallback = `Good ${tod}${firstName ? `, ${firstName}` : ""}`;

  return (
    <div className="flex flex-col items-center">
      {/* Living symbol */}
      <div className="relative mb-8">
        <pre className="text-[12px] leading-[12px] whitespace-pre text-foreground/30 select-none scale-[0.6] sm:scale-100 origin-center">
          {symbol.base}
        </pre>
        {/* Glow */}
        <div
          className="absolute inset-0 pointer-events-none -z-10 blur-xl"
          style={{
            background: "radial-gradient(ellipse at center, var(--color-primary)/0.08, transparent 60%)",
          }}
        />
      </div>

      {/* Greeting */}
      <div className="min-h-[3rem] flex items-center justify-center">
        {briefLoading && (
          <div className="flex gap-2">
            <span className="w-1.5 h-1.5 rounded-full bg-primary/20 animate-pulse" />
            <span className="w-1.5 h-1.5 rounded-full bg-primary/20 animate-pulse [animation-delay:150ms]" />
            <span className="w-1.5 h-1.5 rounded-full bg-primary/20 animate-pulse [animation-delay:300ms]" />
          </div>
        )}
        {brief && !briefLoading && (
          <p className="text-xl text-foreground/80 font-sans text-center leading-relaxed max-w-md animate-fade-in">
            {brief.message}
          </p>
        )}
        {!brief && !briefLoading && (
          <p className="text-xl text-foreground/50 font-sans animate-fade-in">
            {fallback}
          </p>
        )}
      </div>
    </div>
  );
}
