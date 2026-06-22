import { useState, type FormEvent } from "react";
import type { NodeProps } from "@xyflow/react";
import type { TasksNode } from "./node-types";
import { formatDueDate, PRIORITY_INDICATOR } from "../helpers";
import { NodeShell } from "./NodeShell";

export function TasksNode({ data }: NodeProps<TasksNode>) {
  const {
    tasks,
    currentFocus,
    onNavigate,
    onToggleTask,
    onDeleteTask,
    onAddTask,
    onClose,
  } = data;
  const [newTask, setNewTask] = useState("");
  const pendingTasks = tasks.filter((t) => !t.done).slice(0, 5);
  const doneTasks = tasks.filter((t) => t.done).slice(0, 3);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const text = newTask.trim();
    if (!text) return;
    onAddTask(text);
    setNewTask("");
  };

  return (
    <NodeShell
      title="Today's Task"
      onClose={onClose}
      className="w-72"
      actions={[
        {
          id: "view-all",
          label: "View all →",
          onClick: () => onNavigate("/tasks"),
        },
      ]}
    >
      <div className="p-3 space-y-3">
        {currentFocus && (
          <div>
            <p className="font-mono text-[9px] tracking-wider text-muted-foreground/40 mb-1">
              FOCUS
            </p>
            <p className="text-sm text-foreground">{currentFocus}</p>
          </div>
        )}

        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            type="text"
            value={newTask}
            onChange={(e) => setNewTask(e.target.value)}
            placeholder="Add a task..."
            className="flex-1 bg-transparent border border-border px-2 py-1 text-xs text-foreground placeholder:text-muted-foreground/20 outline-none focus:border-muted-foreground/30 transition-colors"
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

        <div className="space-y-1.5 max-h-48 overflow-y-auto pr-1">
          {pendingTasks.map((task) => (
            <div key={task.id} className="flex items-start gap-2 group">
              <button
                onClick={() => onToggleTask(task)}
                className="w-3.5 h-3.5 border border-border shrink-0 mt-0.5 hover:border-primary/50 hover:bg-primary/[0.06] transition-colors cursor-pointer"
              />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5">
                  {task.priority > 0 && (
                    <span
                      className={`w-1 h-1 shrink-0 ${PRIORITY_INDICATOR[task.priority]?.dot}`}
                      title={PRIORITY_INDICATOR[task.priority]?.label}
                    />
                  )}
                  <span className="text-xs text-foreground/80 truncate">
                    {task.text}
                  </span>
                </div>
                {task.dueDate && (
                  <p
                    className={`font-mono text-[8px] mt-0.5 tracking-wider ${
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
                className="font-mono text-[8px] text-transparent group-hover:text-muted-foreground/30 hover:!text-muted-foreground transition-colors tracking-wider"
              >
                DEL
              </button>
            </div>
          ))}

          {doneTasks.map((task) => (
            <div key={task.id} className="flex items-start gap-2 opacity-30 group">
              <button
                onClick={() => onToggleTask(task)}
                className="w-3.5 h-3.5 bg-success/20 border border-success/30 shrink-0 mt-0.5 flex items-center justify-center cursor-pointer hover:bg-success/30 transition-colors"
              >
                <span className="w-1 h-1 bg-success/60" />
              </button>
              <span className="text-xs line-through flex-1 truncate">
                {task.text}
              </span>
              <button
                onClick={() => onDeleteTask(task.id)}
                className="font-mono text-[8px] text-transparent group-hover:text-muted-foreground/30 hover:!text-muted-foreground transition-colors tracking-wider"
              >
                DEL
              </button>
            </div>
          ))}

          {pendingTasks.length === 0 && doneTasks.length === 0 && (
            <p className="font-mono text-[10px] text-muted-foreground/30 tracking-wider">
              ALL CAUGHT UP
            </p>
          )}
        </div>
      </div>
    </NodeShell>
  );
}
