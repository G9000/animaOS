import type { FormEvent } from "react";
import type { HomeData, TaskItem } from "../../lib/api";
import { formatDueDate, PRIORITY_INDICATOR } from "./helpers";

interface DashboardTasksCardProps {
  home: HomeData | null;
  newTask: string;
  openTasks: TaskItem[];
  doneTasks: TaskItem[];
  onNewTaskChange: (value: string) => void;
  onAddTask: (e: FormEvent) => void;
  onToggleTask: (task: TaskItem) => void;
  onDeleteTask: (id: number) => void;
}

export function DashboardTasksCard({
  home,
  newTask,
  openTasks,
  doneTasks,
  onNewTaskChange,
  onAddTask,
  onToggleTask,
  onDeleteTask,
}: DashboardTasksCardProps) {
  return (
    <div className="bg-bg-card border border-border rounded-xl p-5 space-y-4">
      {home?.currentFocus && (
        <div>
          <p className="text-[10px] uppercase tracking-wider text-text-muted/50 mb-1.5">
            Focusing on
          </p>
          <p className="text-sm text-text">{home.currentFocus}</p>
        </div>
      )}

      <div>
        {home?.currentFocus && (
          <div className="border-t border-border -mx-5 mb-4" />
        )}

        <form onSubmit={onAddTask} className="flex gap-2 mb-3">
          <input
            type="text"
            value={newTask}
            onChange={(e) => onNewTaskChange(e.target.value)}
            placeholder="Add a task..."
            className="flex-1 bg-transparent border border-border rounded-lg px-3 py-1.5 text-sm text-text placeholder:text-text-muted/25 outline-none focus:border-text-muted/30 transition-colors"
          />
          {newTask.trim() && (
            <button
              type="submit"
              className="text-xs text-primary hover:text-text px-2 transition-colors"
            >
              +
            </button>
          )}
        </form>

        <div className="space-y-1.5">
          {openTasks.map((task) => (
            <div key={task.id} className="flex items-start gap-2.5 group">
              <button
                onClick={() => onToggleTask(task)}
                className="w-4 h-4 rounded-full border border-border shrink-0 mt-0.5 hover:border-primary/60 hover:bg-primary/10 transition-colors cursor-pointer"
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  {task.priority > 0 && (
                    <span
                      className={`w-1.5 h-1.5 rounded-full shrink-0 ${PRIORITY_INDICATOR[task.priority]?.dot}`}
                      title={PRIORITY_INDICATOR[task.priority]?.label}
                    />
                  )}
                  <span className="text-sm text-text/80 truncate">{task.text}</span>
                </div>
                {task.dueDate && (
                  <p
                    className={`text-[10px] mt-0.5 ${
                      new Date(task.dueDate).getTime() < Date.now()
                        ? "text-red-400/80"
                        : "text-text-muted/40"
                    }`}
                  >
                    {formatDueDate(task.dueDate)}
                  </p>
                )}
              </div>
              <button
                onClick={() => onDeleteTask(task.id)}
                className="text-[10px] text-text-muted/0 group-hover:text-text-muted/40 hover:!text-text-muted transition-colors"
              >
                ×
              </button>
            </div>
          ))}
          {doneTasks.slice(0, 3).map((task) => (
            <div key={task.id} className="flex items-start gap-2.5 opacity-40 group">
              <button
                onClick={() => onToggleTask(task)}
                className="w-4 h-4 rounded-full bg-success/20 border border-success/30 shrink-0 mt-0.5 flex items-center justify-center cursor-pointer hover:bg-success/30 transition-colors"
              >
                <span className="w-1.5 h-1.5 rounded-full bg-success/60" />
              </button>
              <span className="text-sm line-through flex-1 truncate">{task.text}</span>
              <button
                onClick={() => onDeleteTask(task.id)}
                className="text-[10px] text-text-muted/0 group-hover:text-text-muted/40 hover:!text-text-muted transition-colors"
              >
                ×
              </button>
            </div>
          ))}
          {doneTasks.length > 3 && (
            <p className="text-[10px] text-text-muted/30 pl-6.5">
              +{doneTasks.length - 3} completed
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
