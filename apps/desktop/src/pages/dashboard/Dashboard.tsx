import { useEffect, useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import type { Greeting, TaskItem } from "@anima/api-client";
import { api } from "../../lib/api";
import { PromptInput } from "@anima/standard-templates";
import { DashboardGreeting } from "./DashboardGreeting";

const GREETING_CACHE_KEY = "anima_dashboard_greeting";
const GREETING_CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutes

function getCachedGreeting(): { greeting: Greeting; ts: number } | null {
  try {
    const raw = sessionStorage.getItem(GREETING_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (Date.now() - parsed.ts < GREETING_CACHE_TTL_MS) {
      return parsed;
    }
  } catch { /* ignore */ }
  return null;
}

function setCachedGreeting(greeting: Greeting): void {
  try {
    sessionStorage.setItem(GREETING_CACHE_KEY, JSON.stringify({ greeting, ts: Date.now() }));
  } catch { /* ignore */ }
}

function useClock() {
  const [time, setTime] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 30_000);
    return () => clearInterval(id);
  }, []);
  return time;
}

export default function Dashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const now = useClock();
  const [brief, setBrief] = useState<Greeting | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [agentName, setAgentName] = useState("Anima");
  const [tasks, setTasks] = useState<TaskItem[]>([]);

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

  // Load greeting (cached)
  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;

    const cached = getCachedGreeting();
    if (cached) {
      setBrief(cached.greeting);
    } else {
      setBriefLoading(true);
      api.chat
        .greeting(user.id)
        .then((g) => {
          if (!active) return;
          setBrief(g);
          setCachedGreeting(g);
        })
        .catch(() => {
          if (!active) return;
          api.chat
            .brief(user.id)
            .then((b) => {
              if (!active) return;
              const fallback: Greeting = {
                message: b.message,
                llmGenerated: false,
                context: {
                  ...b.context,
                  overdueTasks: 0,
                  upcomingDeadlines: [],
                },
              };
              setBrief(fallback);
              setCachedGreeting(fallback);
            })
            .catch(() => {});
        })
        .finally(() => {
          if (active) setBriefLoading(false);
        });
    }

    return () => {
      active = false;
    };
  }, [user?.id, needsSetup]);

  // Load pending tasks
  useEffect(() => {
    if (user?.id == null || needsSetup !== false) return;
    let active = true;

    api.tasks.list(user.id)
      .then((taskList) => {
        if (!active) return;
        setTasks((taskList ?? []).filter((t) => !t.done).slice(0, 5));
      })
      .catch(() => {});

    return () => {
      active = false;
    };
  }, [user?.id, needsSetup]);

  const handlePromptSubmit = (value: string) => {
    navigate(`/chat?msg=${encodeURIComponent(value)}`);
  };

  // Agent setup incomplete → send to Init flow
  if (needsSetup && user?.id != null) {
    return <Navigate to="/init" replace />;
  }

  // Loading state while checking setup
  if (needsSetup === null) {
    return <div className="h-full" />;
  }

  const openCount = tasks.length;
  const timeStr = now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }).toLowerCase();

  return (
    <div className="h-full flex flex-col items-center justify-center px-6">
      <div className="w-full max-w-xl space-y-8">
        {/* Greeting */}
        <DashboardGreeting
          userName={user?.name}
          agentName={agentName}
          briefLoading={briefLoading}
          brief={brief}
        />

        {/* Input */}
        <PromptInput
          agentName={agentName}
          onSubmit={handlePromptSubmit}
          size="lg"
        />

        {/* Status strip */}
        <div className="flex items-center justify-between border-t border-border pt-3">
          <span className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/40">
            {timeStr}
          </span>
          <button
            onClick={() => navigate("/tasks")}
            className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/40 hover:text-foreground transition-colors"
          >
            {openCount > 0 ? `${openCount} pending` : "all caught up"}
          </button>
        </div>
      </div>
    </div>
  );
}
