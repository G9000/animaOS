import type { HomeData, TaskItem, MemoryEpisodeData } from "@anima/api-client";
import { DashboardDiary } from "./DashboardDiary";

interface DashboardBoardProps {
  home: HomeData | null;
  tasks: TaskItem[];
  episodes: MemoryEpisodeData[];
  agentName: string;
  onNavigate: (path: string) => void;
}

function StatCard({
  label,
  value,
  onClick,
}: {
  label: string;
  value: number | string;
  onClick?: () => void;
}) {
  return (
    <button
      onClick={onClick}
      disabled={!onClick}
      className={`flex flex-col items-start gap-1 bg-card/50 border border-border/60 p-3 text-left transition-colors ${
        onClick ? "hover:border-border hover:bg-card" : ""
      }`}
    >
      <span className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/40">
        {label}
      </span>
      <span className="text-lg font-sans text-foreground leading-none">
        {value}
      </span>
    </button>
  );
}

export function DashboardBoard({
  home,
  tasks,
  episodes,
  agentName,
  onNavigate,
}: DashboardBoardProps) {
  const pendingTasks = tasks.filter((t) => !t.done).slice(0, 3);

  return (
    <div className="space-y-6 animate-fade-in">
      {/* Stats grid */}
      {home && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <StatCard
            label="Memories"
            value={home.memoryCount}
            onClick={() => onNavigate("/memory")}
          />
          <StatCard
            label="Messages"
            value={home.messageCount}
            onClick={() => onNavigate("/chat")}
          />
          <StatCard
            label="Streak"
            value={home.journalStreak}
          />
          <StatCard
            label="Journal"
            value={home.journalTotal}
          />
        </div>
      )}

      {/* Focus + Tasks row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {/* Current focus */}
        <div className="bg-card/50 border border-border/60 p-3">
          <span className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/40 block mb-2">
            Current Focus
          </span>
          {home?.currentFocus ? (
            <p className="text-sm text-foreground leading-relaxed">
              {home.currentFocus}
            </p>
          ) : (
            <p className="font-mono text-[10px] text-muted-foreground/30 tracking-wider">
              NO ACTIVE FOCUS
            </p>
          )}
        </div>

        {/* Recent tasks */}
        <div className="bg-card/50 border border-border/60 p-3">
          <div className="flex items-center justify-between mb-2">
            <span className="font-mono text-[9px] tracking-[0.22em] uppercase text-muted-foreground/40">
              Tasks
            </span>
            <button
              onClick={() => onNavigate("/tasks")}
              className="font-mono text-[9px] text-muted-foreground/30 hover:text-foreground tracking-wider transition-colors"
            >
              VIEW ALL →
            </button>
          </div>
          {pendingTasks.length > 0 ? (
            <div className="space-y-1.5">
              {pendingTasks.map((task) => (
                <div key={task.id} className="flex items-start gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-primary/60 mt-1.5 shrink-0" />
                  <span className="text-sm text-foreground/80 leading-snug truncate">
                    {task.text}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <p className="font-mono text-[10px] text-muted-foreground/30 tracking-wider">
              ALL CAUGHT UP
            </p>
          )}
        </div>
      </div>

      {/* Diary */}
      <DashboardDiary episodes={episodes} agentName={agentName} />
    </div>
  );
}
