import type { Greeting } from "@anima/api-client";
import { DotLoader } from "@anima/standard-templates";

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
  const firstName = userName?.split(" ")[0];
  const fallback = `Good ${tod}${firstName ? `, ${firstName}` : ""}.`;

  return (
    <div className="mb-6 text-center">
      {briefLoading ? (
        <DotLoader />
      ) : (
        <p className="font-mono text-ui text-muted-foreground leading-relaxed animate-fade-in">
          {brief?.message ?? fallback}
        </p>
      )}
    </div>
  );
}
