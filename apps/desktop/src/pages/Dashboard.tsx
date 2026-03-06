import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { api, type DailyBrief, type Nudge } from "../lib/api";

export default function Dashboard() {
  const { user } = useAuth();
  const [brief, setBrief] = useState<DailyBrief | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [nudges, setNudges] = useState<Nudge[]>([]);
  const [dismissedNudges, setDismissedNudges] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!user?.id) return;
    let active = true;

    setBriefLoading(true);
    api.chat
      .brief(user.id)
      .then((b) => { if (active) setBrief(b); })
      .catch(() => {})
      .finally(() => { if (active) setBriefLoading(false); });

    api.chat
      .nudges(user.id)
      .then((res) => { if (active) setNudges(res.nudges); })
      .catch(() => {});

    return () => { active = false; };
  }, [user?.id]);

  const activeNudges = nudges.filter((n) => !dismissedNudges.has(n.type));

  return (
    <div className="h-full flex items-center justify-center px-8">
      <div className="w-full max-w-md space-y-8 -mt-16">
        {/* Greeting */}
        <div className="text-center space-y-4">
          <div className="text-2xl text-(--color-text-muted)/15 tracking-widest select-none">
            ◈
          </div>
          {briefLoading && (
            <p className="text-sm text-(--color-text-muted) animate-pulse">
              ...
            </p>
          )}
          {brief && !briefLoading && (
            <p className="text-base text-(--color-text) leading-relaxed">
              {brief.message}
            </p>
          )}
          {!brief && !briefLoading && (
            <p className="text-base text-(--color-text-muted)">
              How was today?
            </p>
          )}
        </div>

        {/* Chat entry */}
        <div className="flex justify-center">
          <Link
            to="/chat"
            className="text-xs text-(--color-text-muted) hover:text-(--color-text) uppercase tracking-widest transition-colors"
          >
            Talk to ANIMA →
          </Link>
        </div>

        {/* Nudges — only if present */}
        {activeNudges.length > 0 && (
          <div className="space-y-2 pt-4">
            {activeNudges.map((nudge) => (
              <div
                key={nudge.type}
                className="flex items-center justify-between gap-3 px-4 py-2 border border-(--color-border) rounded-sm"
              >
                <span className="text-xs text-(--color-text-muted)">
                  {nudge.message}
                </span>
                <button
                  onClick={() =>
                    setDismissedNudges((prev) => new Set([...prev, nudge.type]))
                  }
                  className="text-[10px] text-(--color-text-muted)/40 hover:text-(--color-text-muted) uppercase tracking-wider shrink-0"
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
