import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import type { Greeting, Nudge } from "@anima/api-client";
import { api } from "../../lib/api";
import { DashboardGreeting } from "./DashboardGreeting";
import { getTimeOfDay } from "./helpers";

export default function Dashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [brief, setBrief] = useState<Greeting | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [nudges, setNudges] = useState<Nudge[]>([]);
  const [dismissedNudges, setDismissedNudges] = useState<Set<string>>(new Set());
  const [needsSetup, setNeedsSetup] = useState<boolean | null>(null);
  const [agentName, setAgentName] = useState("Anima");

  const [input, setInput] = useState("");
  const [attachOpen, setAttachOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const attachRef = useRef<HTMLDivElement>(null);

  const MAX_ROWS = 6;

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
        api.chat.brief(user.id).then((b) => {
          if (active) setBrief({ message: b.message, llmGenerated: false, context: { ...b.context, overdueTasks: 0, upcomingDeadlines: [] } });
        }).catch(() => {});
      })
      .finally(() => {
        if (active) setBriefLoading(false);
      });

    api.chat
      .nudges(user.id)
      .then((res) => {
        if (active) setNudges(res.nudges);
      })
      .catch(() => {});


    return () => {
      active = false;
    };
  }, [user?.id, needsSetup]);

  useEffect(() => {
    if (!attachOpen) return;
    const handleClick = (e: MouseEvent) => {
      if (attachRef.current && !attachRef.current.contains(e.target as Node)) {
        setAttachOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [attachOpen]);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    navigate(`/chat?msg=${encodeURIComponent(input.trim())}`);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const autoResize = () => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const lineHeight = 20;
    const maxHeight = lineHeight * MAX_ROWS;
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  };

  const activeNudges = nudges.filter((n) => !dismissedNudges.has(n.type));
  const tod = getTimeOfDay();

  // Agent setup incomplete → send to Init flow
  if (needsSetup && user?.id != null) {
    return <Navigate to="/init" replace />;
  }

  // Loading state while checking setup
  if (needsSetup === null) {
    return <div className="h-full" />;
  }

  return (
    <div className="h-full overflow-y-auto relative">
      {/* Atmosphere — subtle radial glow */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background: "radial-gradient(ellipse at 50% 40%, rgba(94,160,171,0.04) 0%, transparent 70%)",
        }}
      />

      <div className="max-w-md mx-auto px-6 h-full flex flex-col items-center justify-center relative z-10">

        <DashboardGreeting
          userName={user?.name}
          tod={tod}
          briefLoading={briefLoading}
          brief={brief}
        />

        {/* Prompt — borderless, just a bottom line */}
        <form onSubmit={handleSubmit} className="w-full mt-10">
          <div className="relative">
            <div className="flex items-end gap-2">
              {/* Attach */}
              <div ref={attachRef} className="relative shrink-0 mb-1">
                <button
                  type="button"
                  onClick={() => setAttachOpen((v) => !v)}
                  className={`w-7 h-7 flex items-center justify-center transition-all ${
                    attachOpen
                      ? "text-foreground/50"
                      : "text-foreground/15 hover:text-foreground/40"
                  }`}
                >
                  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="12" y1="5" x2="12" y2="19" />
                    <line x1="5" y1="12" x2="19" y2="12" />
                  </svg>
                </button>

                {attachOpen && (
                  <div className="absolute bottom-full left-0 mb-2 w-44 bg-card border border-border shadow-[0_4px_24px_rgba(0,0,0,0.25)] py-1 animate-fade-in z-50">
                    <button type="button" onClick={() => setAttachOpen(false)} className="w-full flex items-center gap-3 px-3 py-2 text-left text-xs text-foreground/40 hover:text-foreground/70 hover:bg-accent transition-colors font-sans">
                      <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="m21 15-5-5L5 21" /></svg>
                      Image
                    </button>
                    <button type="button" onClick={() => setAttachOpen(false)} className="w-full flex items-center gap-3 px-3 py-2 text-left text-xs text-foreground/40 hover:text-foreground/70 hover:bg-accent transition-colors font-sans">
                      <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 18 8.84l-8.59 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" /></svg>
                      File
                    </button>
                    <button type="button" onClick={() => setAttachOpen(false)} className="w-full flex items-center gap-3 px-3 py-2 text-left text-xs text-foreground/40 hover:text-foreground/70 hover:bg-accent transition-colors font-sans">
                      <svg className="w-3.5 h-3.5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" /><polyline points="14 2 14 8 20 8" /></svg>
                      Document
                    </button>
                  </div>
                )}
              </div>

              {/* Textarea */}
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => { setInput(e.target.value); autoResize(); }}
                onKeyDown={handleKeyDown}
                placeholder={`Talk to ${agentName}...`}
                rows={1}
                className="flex-1 bg-transparent text-sm text-foreground font-sans placeholder:text-foreground/15 outline-none resize-none pb-1 leading-6"
              />

              {/* Right side controls */}
              <div className="flex items-center gap-0.5 shrink-0 mb-1">
                <button type="button" className="w-7 h-7 flex items-center justify-center text-foreground/15 hover:text-foreground/40 transition-all" title="Voice input">
                  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                    <line x1="12" x2="12" y1="19" y2="22" />
                  </svg>
                </button>
                {input.trim() && (
                  <button
                    type="submit"
                    className="w-7 h-7 flex items-center justify-center text-foreground/25 hover:text-foreground/50 transition-all animate-fade-in"
                  >
                    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M5 12h14" />
                      <path d="m12 5 7 7-7 7" />
                    </svg>
                  </button>
                )}
              </div>
            </div>

            {/* Bottom line */}
            <div className="h-px bg-foreground/5 mt-1 transition-colors group-focus-within:bg-foreground/15" />
          </div>
        </form>

        {/* Nudges */}
        {activeNudges.length > 0 && (
          <div className="mt-6 space-y-2 w-full">
            {activeNudges.map((nudge) => (
              <div
                key={nudge.type}
                className="flex items-center justify-between gap-3 py-1.5 animate-fade-in"
              >
                <span className="text-xs text-foreground/25 font-sans">{nudge.message}</span>
                <button
                  onClick={() => setDismissedNudges((prev) => new Set([...prev, nudge.type]))}
                  className="text-foreground/10 hover:text-foreground/30 text-sm transition-colors shrink-0"
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
