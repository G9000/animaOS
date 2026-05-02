import type { Greeting } from "@anima/api-client";
import { DotLoader } from "@anima/standard-templates";

interface DashboardGreetingProps {
  userName?: string;
  agentName?: string;
  briefLoading: boolean;
  brief: Greeting | null;
}

export function DashboardGreeting({
  userName,
  agentName = "Anima",
  briefLoading,
  brief,
}: DashboardGreetingProps) {
  const firstName = userName?.split(" ")[0];

  // Prefer the LLM-generated greeting, fallback to a friendly static one
  const message = brief?.message ?? `Hi${firstName ? ` ${firstName}` : ""}, how can I help you today?`;

  return (
    <div>
      {briefLoading ? (
        <DotLoader />
      ) : (
        <div className="space-y-1 animate-fade-in">
          <h1 className="text-2xl font-sans text-foreground leading-tight">
            {message}
          </h1>
          {brief?.llmGenerated && (
            <p className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/40">
              from {agentName}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
