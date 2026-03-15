import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { api, type Greeting, type HomeData, type Nudge, type TaskItem } from "../lib/api";
import { DashboardGreeting } from "./dashboard/DashboardGreeting";
import { DashboardNudges } from "./dashboard/DashboardNudges";
import { DashboardPromptForm } from "./dashboard/DashboardPromptForm";
import { DashboardTasksCard } from "./dashboard/DashboardTasksCard";
import { getTimeOfDay } from "./dashboard/helpers";

export default function Dashboard() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [brief, setBrief] = useState<Greeting | null>(null);
  const [briefLoading, setBriefLoading] = useState(false);
  const [nudges, setNudges] = useState<Nudge[]>([]);
  const [dismissedNudges, setDismissedNudges] = useState<Set<string>>(new Set());
  const [home, setHome] = useState<HomeData | null>(null);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [newTask, setNewTask] = useState("");
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (user?.id == null) return;
    let active = true;

    setBriefLoading(true);
    api.chat
      .greeting(user.id)
      .then((g) => {
        if (active) setBrief(g);
      })
      .catch(() => {
        // Fall back to static brief if greeting fails
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

    api.chat
      .home(user.id)
      .then((data) => {
        if (active) setHome(data);
      })
      .catch(() => {});

    api.tasks
      .list(user.id)
      .then((rows) => {
        if (active) setTasks(rows);
      })
      .catch(() => {});

    return () => {
      active = false;
    };
  }, [user?.id]);

  const toggleTask = async (task: TaskItem) => {
    const updated = await api.tasks.update(task.id, { done: !task.done });
    setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
  };

  const handleAddTask = async (e: FormEvent) => {
    e.preventDefault();
    if (!newTask.trim() || user?.id == null) return;
    // Send the full input as both text and dueDateRaw — server extracts the date.
    const created = await api.tasks.create(
      user.id,
      newTask.trim(),
      undefined,
      undefined,
      newTask.trim(),
    );
    setTasks((prev) => [...prev, created]);
    setNewTask("");
  };

  const deleteTask = async (id: number) => {
    await api.tasks.delete(id);
    setTasks((prev) => prev.filter((t) => t.id !== id));
  };

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    navigate(`/chat?msg=${encodeURIComponent(input.trim())}`);
  };

  const activeNudges = nudges.filter((n) => !dismissedNudges.has(n.type));
  const openTasks = tasks.filter((t) => !t.done);
  const doneTasks = tasks.filter((t) => t.done);
  const tod = getTimeOfDay();

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-xl mx-auto px-6 py-12 space-y-10">
        <DashboardGreeting
          userName={user?.name}
          tod={tod}
          briefLoading={briefLoading}
          brief={brief}
        />

        <DashboardPromptForm
          inputRef={inputRef}
          input={input}
          onInputChange={setInput}
          onSubmit={handleSubmit}
        />

        <DashboardNudges
          nudges={activeNudges}
          onDismiss={(type) =>
            setDismissedNudges((prev) => new Set([...prev, type]))
          }
        />

        <DashboardTasksCard
          home={home}
          newTask={newTask}
          openTasks={openTasks}
          doneTasks={doneTasks}
          onNewTaskChange={setNewTask}
          onAddTask={handleAddTask}
          onToggleTask={toggleTask}
          onDeleteTask={deleteTask}
        />

        {/* Stats */}
        {home && (
          <div className="grid grid-cols-3 gap-3">
            <div className="bg-bg-card border border-border rounded-xl px-4 py-3 text-center">
              <p className="text-lg text-text font-medium">{home.messageCount}</p>
              <p className="text-[10px] text-text-muted/50 uppercase tracking-wider mt-0.5">Messages</p>
            </div>
            <div className="bg-bg-card border border-border rounded-xl px-4 py-3 text-center">
              <p className="text-lg text-text font-medium">{home.memoryCount}</p>
              <p className="text-[10px] text-text-muted/50 uppercase tracking-wider mt-0.5">Memories</p>
            </div>
            <div className="bg-bg-card border border-border rounded-xl px-4 py-3 text-center">
              <p className="text-lg text-text font-medium">
                {home.journalStreak > 0 ? home.journalStreak : home.journalTotal}
              </p>
              <p className="text-[10px] text-text-muted/50 uppercase tracking-wider mt-0.5">
                {home.journalStreak > 0 ? `Day streak` : "Journal days"}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
