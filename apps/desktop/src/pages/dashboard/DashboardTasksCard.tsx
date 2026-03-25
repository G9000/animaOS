import type { FormEvent } from "react";
import type { HomeData, TaskItem } from "@anima/api-client";
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
    <div className="bg-card border border-border p-5 space-y-4">
      {home?.currentFocus && (
        <div>
          <p className="font-mono text-[9px] tracking-wider text-muted-foreground/40 mb-1.5">
            FOCUS
          </p>
          <p className="text-sm text-foreground">{home.currentFocus}</p>
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
            className="flex-1 bg-transparent border border-border px-3 py-1.5 text-sm text-foreground placeholder:text-muted-foreground/20 outline-none focus:border-text-muted/30 transition-colors"
          />
          {newTask.trim() && (
            <button
              type="submit"
              className="font-mono text-[9px] text-primary/50 hover:text-primary px-2 transition-colors tracking-wider"
            >
              ADD
            </button>
          )}
        </form>

        <div className="space-y-1">
          {openTasks.map((task) => (
            <div key={task.id} className="flex items-start gap-2.5 group">
              <button
                onClick={() => onToggleTask(task)}
                className="w-3.5 h-3.5 border border-border shrink-0 mt-1 hover:border-primary/50 hover:bg-primary/[0.06] transition-colors cursor-pointer"
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  {task.priority > 0 && (
                    <span
                      className={`w-1 h-1 shrink-0 ${PRIORITY_INDICATOR[task.priority]?.dot}`}
                      title={PRIORITY_INDICATOR[task.priority]?.label}
                    />
                  )}
                  <span className="text-sm text-foreground/80 truncate">{task.text}</span>
                </div>
                {task.dueDate && (
                  <p
                    className={`font-mono text-[9px] mt-0.5 tracking-wider ${
                      new Date(task.dueDate).getTime() < Date.now()
                        ? "text-destructive/70"
                        : "text-muted-foreground/30"
                    }`}
                  >
                    {formatDueDate(task.dueDate)}
                  </p>
                )}
              </div>
              <button
                onClick={() => onDeleteTask(task.id)}
                className="font-mono text-[9px] text-transparent group-hover:text-muted-foreground/30 hover:!text-muted-foreground transition-colors tracking-wider"
              >
                DEL
              </button>
            </div>
          ))}
          {doneTasks.slice(0, 3).map((task) => (
            <div key={task.id} className="flex items-start gap-2.5 opacity-30 group">
              <button
                onClick={() => onToggleTask(task)}
                className="w-3.5 h-3.5 bg-success/20 border border-success/30 shrink-0 mt-1 flex items-center justify-center cursor-pointer hover:bg-success/30 transition-colors"
              >
                <span className="w-1 h-1 bg-success/60" />
              </button>
              <span className="text-sm line-through flex-1 truncate">{task.text}</span>
              <button
                onClick={() => onDeleteTask(task.id)}
                className="font-mono text-[9px] text-transparent group-hover:text-muted-foreground/30 hover:!text-muted-foreground transition-colors tracking-wider"
              >
                DEL
              </button>
            </div>
          ))}
          {doneTasks.length > 3 && (
            <p className="font-mono text-[9px] text-muted-foreground/20 pl-6 tracking-wider">
              +{doneTasks.length - 3} COMPLETED
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
