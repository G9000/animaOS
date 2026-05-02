import { useEffect, useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import type { Greeting } from "@anima/api-client";
import { api } from "../../lib/api";
import { useAnimaSymbol, PromptInput } from "@anima/standard-templates";
import { DashboardGreeting } from "./DashboardGreeting";
import { getTimeOfDay } from "./helpers";

export default function Dashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [brief, setBrief] = useState<Greeting | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [agentName, setAgentName] = useState("Anima");

  // Check if agent setup is needed
  useEffect(() => {
    if (user?.id == null) return;
    api.consciousness
      .getAgentProfile(user.id)
      .then((profile) => {
        setNeedsSetup(!profile.setupComplete);
        if (profile.agentName) setAgentName(profile.agentName);
      })
      .catch(() => setNeedsSetup(true));
  }, [user?.id]);

  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;

    setBriefLoading(true);
    api.chat
      .greeting(user.id)
      .then((g) => {
        if (active) setBrief(g);
      })
      .catch(() => {
        api.chat
          .brief(user.id)
          .then((b) => {
            if (active)
              setBrief({
                message: b.message,
                llmGenerated: false,
                context: {
                  ...b.context,
                  overdueTasks: 0,
                  upcomingDeadlines: [],
                },
              });
          })
          .catch(() => {});
      })
      .finally(() => {
        if (active) setBriefLoading(false);
      });

    return () => {
      active = false;
    };
  }, [user?.id, needsSetup]);

  const handlePromptSubmit = (value: string) => {
    navigate(`/chat?msg=${encodeURIComponent(value)}`);
  };

  const tod = getTimeOfDay();
  const symbol = useAnimaSymbol(briefLoading ? 2 : 0.6);

  // Agent setup incomplete → send to Init flow
  if (needsSetup && user?.id != null) {
    return <Navigate to="/init" replace />;
  }

  // Loading state while checking setup
  if (needsSetup === null) {
    return <div className="h-full" />;
  }

  return (
    <div className="h-full flex flex-col items-center justify-center gap-8 px-8 py-8 overflow-hidden">
      {/* Symbol — hardware screen vibe */}
      <div className="relative pointer-events-none">
        <div className="hw-module p-6">
          <pre
            className="whitespace-pre leading-none text-foreground/25 bg-transparent select-none"
            style={{ fontSize: "5.5px", lineHeight: "5.5px" }}
          >
            {symbol.base}
          </pre>
        </div>
      </div>

      {/* Greeting + input */}
      <div className="w-full max-w-md space-y-6">
        <DashboardGreeting
          userName={user?.name}
          tod={tod}
          briefLoading={briefLoading}
          brief={brief}
        />
        <PromptInput agentName={agentName} onSubmit={handlePromptSubmit} />
      </div>
    </div>
  );
}
