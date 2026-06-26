import { useState, type FormEvent } from "react";
import { NodeResizer, type NodeProps } from "@xyflow/react";
import type { TasksNode } from "./node-types";
import { formatDueDate } from "../helpers";

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

  const pendingTasks = tasks.filter((t) => !t.done).slice(0, 10);
  const doneTasks = tasks.filter((t) => t.done).slice(0, 3);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    const text = newTask.trim();
    if (!text) return;
    onAddTask(text);
    setNewTask("");
  };

  const priorityColor = (p: number) => {
    if (p === 2) return "bg-destructive/75";
    if (p === 1) return "bg-amber-400/80";
    return null;
  };

  return (
    <>
      <NodeResizer
        minWidth={220}
        minHeight={160}
        maxWidth={480}
        maxHeight={800}
        lineStyle={{ borderColor: "var(--border)", borderWidth: 1, opacity: 0.35 }}
        handleStyle={{
          width: 8,
          height: 8,
          borderRadius: 2,
          border: "1px solid var(--border)",
          background: "var(--background)",
          opacity: 0.55,
        }}
      />

      <div className="group relative w-full h-full overflow-visible">

        {/* Close */}
        <button
          onClick={onClose}
          className="absolute -top-5 right-0 z-20 h-4 px-1.5 flex items-center rounded-sm bg-background/60 border border-foreground/[0.07] font-mono text-[8px] text-foreground/25 hover:text-foreground/60 hover:bg-background/80 opacity-0 group-hover:opacity-100 transition-all duration-200 backdrop-blur-sm"
          aria-label="Close tasks"
        >
          ×
        </button>

        {/* Glass card */}
        <div className="w-full h-full overflow-hidden rounded-xl bg-background/25 backdrop-blur-[36px] border border-foreground/[0.08] shadow-[0_4px_28px_rgba(0,0,0,0.18)] flex flex-col">

          {/* Header */}
          <div className="shrink-0 flex items-center justify-between px-3.5 h-9 border-b border-foreground/[0.06]">
            <span className="font-mono text-[8px] tracking-[0.3em] uppercase text-foreground/30">
              Tasks
            </span>
            <div className="flex items-center gap-3">
              {pendingTasks.length > 0 && (
                <span className="font-mono text-[8px] text-foreground/20">
                  {pendingTasks.length} pending
                </span>
              )}
              <button
                onClick={() => onNavigate("/tasks")}
                className="font-mono text-[7.5px] tracking-wider text-foreground/22 hover:text-foreground/55 transition-colors"
              >
                all →
              </button>
            </div>
          </div>

          {/* Current focus */}
          {currentFocus && (
            <div className="shrink-0 px-3.5 py-2.5 border-b border-foreground/[0.05] bg-accent/[0.04]">
              <span className="font-mono text-[7px] tracking-[0.3em] uppercase text-foreground/22 block mb-1">
                focus
              </span>
              <p className="text-[11px] text-foreground/65 leading-snug">{currentFocus}</p>
            </div>
          )}

          {/* Quick-add */}
          <form
            onSubmit={handleSubmit}
            className="shrink-0 flex items-center gap-2.5 px-3.5 h-9 border-b border-foreground/[0.06]"
          >
            <span className="font-mono text-[11px] text-foreground/18 leading-none">+</span>
            <input
              type="text"
              value={newTask}
              onChange={(e) => setNewTask(e.target.value)}
              placeholder="add a task…"
              className="flex-1 bg-transparent font-mono text-[9px] tracking-wide text-foreground/60 placeholder:text-foreground/18 focus:outline-none"
            />
            {newTask.trim() && (
              <button
                type="submit"
                className="font-mono text-[7.5px] tracking-[0.2em] uppercase text-accent/60 hover:text-accent transition-colors shrink-0"
              >
                add
              </button>
            )}
          </form>

          {/* Task list */}
          <div
            className="flex-1 min-h-0 overflow-y-auto nowheel py-1"
            style={{ scrollbarWidth: "none" }}
          >
            {pendingTasks.length === 0 && doneTasks.length === 0 ? (
              <div className="flex items-center justify-center h-full">
                <p className="font-mono text-[8px] tracking-[0.28em] uppercase text-foreground/18">
                  all caught up
                </p>
              </div>
            ) : (
              <>
                {/* Pending tasks */}
                {pendingTasks.map((task) => {
                  const dot = priorityColor(task.priority);
                  const overdue =
                    task.dueDate &&
                    new Date(task.dueDate).getTime() < Date.now();
                  return (
                    <div
                      key={task.id}
                      className="group/task flex items-start gap-2.5 px-3.5 py-2 hover:bg-foreground/[0.03] transition-colors"
                    >
                      <button
                        onClick={() => onToggleTask(task)}
                        className="w-3.5 h-3.5 shrink-0 mt-0.5 border border-foreground/[0.2] hover:border-accent/60 hover:bg-accent/[0.06] transition-all duration-150"
                        aria-label="Complete task"
                      />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5">
                          {dot && (
                            <span
                              className={`w-[5px] h-[5px] rounded-full shrink-0 ${dot}`}
                            />
                          )}
                          <span className="text-[11px] text-foreground/72 leading-tight truncate">
                            {task.text}
                          </span>
                        </div>
                        {task.dueDate && (
                          <p
                            className={`font-mono text-[7.5px] mt-0.5 tracking-wider ${
                              overdue
                                ? "text-destructive/70"
                                : "text-foreground/22"
                            }`}
                          >
                            {formatDueDate(task.dueDate)}
                          </p>
                        )}
                      </div>
                      <button
                        onClick={() => onDeleteTask(task.id)}
                        className="font-mono text-[10px] text-transparent group-hover/task:text-foreground/18 hover:!text-foreground/50 transition-colors mt-px leading-none"
                        aria-label="Delete task"
                      >
                        ×
                      </button>
                    </div>
                  );
                })}

                {/* Divider */}
                {pendingTasks.length > 0 && doneTasks.length > 0 && (
                  <div className="h-px bg-foreground/[0.05] mx-3.5 my-1" />
                )}

                {/* Done tasks */}
                {doneTasks.map((task) => (
                  <div
                    key={task.id}
                    className="group/task flex items-start gap-2.5 px-3.5 py-1.5 opacity-28 hover:opacity-40 transition-opacity"
                  >
                    <button
                      onClick={() => onToggleTask(task)}
                      className="w-3.5 h-3.5 shrink-0 mt-0.5 border border-foreground/25 bg-foreground/[0.07] flex items-center justify-center transition-all"
                      aria-label="Uncheck task"
                    >
                      <span className="font-mono text-[7px] leading-none text-foreground/55">
                        ✓
                      </span>
                    </button>
                    <span className="text-[11px] text-foreground/55 line-through truncate flex-1 leading-tight mt-0.5">
                      {task.text}
                    </span>
                    <button
                      onClick={() => onDeleteTask(task.id)}
                      className="font-mono text-[10px] text-transparent group-hover/task:text-foreground/22 hover:!text-foreground/50 transition-colors mt-px leading-none"
                      aria-label="Delete task"
                    >
                      ×
                    </button>
                  </div>
                ))}
              </>
            )}
          </div>

        </div>
      </div>
    </>
  );
}
