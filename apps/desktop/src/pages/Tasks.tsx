import { useState, useEffect, useRef } from "react";
import { useAuth } from "../context/AuthContext";
import type { TaskItem } from "@anima/api-client";
import { api } from "../lib/api";

function formatDueDate(iso: string): string {
  const due = new Date(iso);
  if (Number.isNaN(due.getTime())) return "";

  const now = new Date();
  const diffMs = due.getTime() - now.getTime();
  const diffMin = Math.round(diffMs / 60_000);
  const diffHrs = Math.round(diffMs / 3_600_000);

  if (diffMs < 0) {
    const ago = Math.abs(diffMin);
    if (ago < 60) return `${ago}m overdue`;
    const hrsAgo = Math.abs(diffHrs);
    if (hrsAgo < 24) return `${hrsAgo}h overdue`;
    return "overdue";
  }

  if (diffMin < 60) return `in ${diffMin}m`;
  if (diffHrs < 24) return `in ${diffHrs}h`;

  const diffDays = Math.ceil(diffMs / 86_400_000);
  if (diffDays <= 7) {
    const dayName = due.toLocaleDateString("en-US", { weekday: "short" });
    const time = due.toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
    });
    return `${dayName} ${time}`;
  }

  return due.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function isOverdue(iso: string): boolean {
  return new Date(iso).getTime() < Date.now();
}

const PRIORITY_LABELS: Record<number, string> = {
  0: "NORMAL",
  1: "HIGH",
  2: "URGENT",
};
const PRIORITY_DOTS: Record<number, string> = {
  0: "",
  1: "bg-warning",
  2: "bg-destructive",
};

type ViewFilter = "open" | "done" | "all";

export default function Tasks() {
  const { user } = useAuth();
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<ViewFilter>("open");

  const [newText, setNewText] = useState("");
  const [newPriority, setNewPriority] = useState(0);
  const [showCreate, setShowCreate] = useState(false);
  const createInputRef = useRef<HTMLInputElement>(null);

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const [editPriority, setEditPriority] = useState(0);
  const [editDueDate, setEditDueDate] = useState("");
  const editInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (user?.id == null) return;
    setLoading(true);
    api.tasks
      .list(user.id)
      .then(setTasks)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [user?.id]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newText.trim() || user?.id == null) return;
    const created = await api.tasks.create(
      user.id,
      newText.trim(),
      newPriority || undefined,
      undefined,
      newText.trim(),
    );
    setTasks((prev) => [created, ...prev]);
    setNewText("");
    setNewPriority(0);
    setShowCreate(false);
  };

  const toggleDone = async (task: TaskItem) => {
    const updated = await api.tasks.update(task.id, { done: !task.done });
    setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
  };

  const handleDelete = async (id: number) => {
    await api.tasks.delete(id);
    setTasks((prev) => prev.filter((t) => t.id !== id));
    if (editingId === id) setEditingId(null);
  };

  const startEdit = (task: TaskItem) => {
    setEditingId(task.id);
    setEditText(task.text);
    setEditPriority(task.priority);
    setEditDueDate(task.dueDate ?? "");
    setTimeout(() => editInputRef.current?.focus(), 50);
  };

  const cancelEdit = () => {
    setEditingId(null);
  };

  const saveEdit = async () => {
    if (editingId === null) return;
    const updates: {
      text?: string;
      priority?: number;
      dueDate?: string | null;
    } = {};

    const original = tasks.find((t) => t.id === editingId);
    if (!original) return;

    if (editText.trim() && editText.trim() !== original.text) {
      updates.text = editText.trim();
    }
    if (editPriority !== original.priority) {
      updates.priority = editPriority;
    }
    if (editDueDate !== (original.dueDate ?? "")) {
      updates.dueDate = editDueDate.trim() || null;
    }

    if (Object.keys(updates).length === 0) {
      setEditingId(null);
      return;
    }

    const updated = await api.tasks.update(editingId, updates);
    setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)));
    setEditingId(null);
  };

  const handleEditKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      saveEdit();
    }
    if (e.key === "Escape") cancelEdit();
  };

  const filtered = tasks
    .filter((t) => {
      if (filter === "open") return !t.done;
      if (filter === "done") return t.done;
      return true;
    })
    .sort((a, b) => {
      if (!a.done && !b.done) {
        if (b.priority !== a.priority) return b.priority - a.priority;
        if (a.dueDate && b.dueDate) return a.dueDate.localeCompare(b.dueDate);
        if (a.dueDate) return -1;
        if (b.dueDate) return 1;
        return (a.createdAt ?? "").localeCompare(b.createdAt ?? "");
      }
      if (a.done && b.done)
        return (b.completedAt ?? "").localeCompare(a.completedAt ?? "");
      return a.done ? 1 : -1;
    });

  const openCount = tasks.filter((t) => !t.done).length;
  const doneCount = tasks.filter((t) => t.done).length;
  const overdueCount = tasks.filter(
    (t) => !t.done && t.dueDate && isOverdue(t.dueDate),
  ).length;

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-2xl mx-auto px-6 py-10 space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="font-mono text-sm tracking-wider text-foreground">TASKS</h1>
            <p className="font-mono text-[9px] text-muted-foreground/40 mt-0.5 tracking-wider">
              {openCount} OPEN
              {overdueCount > 0 && (
                <span className="text-destructive ml-1.5">
                  | {overdueCount} OVERDUE
                </span>
              )}
              {doneCount > 0 && (
                <span className="ml-1.5">| {doneCount} DONE</span>
              )}
            </p>
          </div>
          <button
            onClick={() => {
              setShowCreate(!showCreate);
              if (!showCreate)
                setTimeout(() => createInputRef.current?.focus(), 50);
            }}
            className="font-mono px-3 py-1.5 text-[9px] tracking-wider border border-border text-muted-foreground hover:text-primary hover:border-primary/30 transition-colors"
          >
            {showCreate ? "CANCEL" : "+ NEW"}
          </button>
        </div>

        {/* Create form */}
        {showCreate && (
          <form
            onSubmit={handleCreate}
            className="bg-card border border-border p-4 space-y-3"
          >
            <input
              ref={createInputRef}
              type="text"
              value={newText}
              onChange={(e) => setNewText(e.target.value)}
              placeholder='E.g. "Buy groceries tomorrow at 5pm"'
              className="w-full bg-transparent border border-border px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground/20 outline-none focus:border-text-muted/30 transition-colors"
            />
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <span className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
                  PRI:
                </span>
                {[0, 1, 2].map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setNewPriority(p)}
                    className={`font-mono px-2 py-0.5 text-[9px] tracking-wider transition-colors ${
                      newPriority === p
                        ? p === 2
                          ? "bg-destructive/10 text-destructive border border-destructive/30"
                          : p === 1
                            ? "bg-warning/10 text-warning border border-warning/30"
                            : "bg-input text-foreground border border-border"
                        : "text-muted-foreground/30 hover:text-muted-foreground/60 border border-transparent"
                    }`}
                  >
                    {PRIORITY_LABELS[p]}
                  </button>
                ))}
              </div>
              <div className="flex-1" />
              <button
                type="submit"
                disabled={!newText.trim()}
                className="font-mono px-4 py-1.5 text-[9px] tracking-wider bg-primary/[0.08] text-primary border border-primary/30 hover:bg-primary/[0.12] transition-colors disabled:opacity-20"
              >
                ADD TASK
              </button>
            </div>
            <p className="font-mono text-[8px] text-muted-foreground/20 tracking-wider">
              TIP: INCLUDE TIME LIKE "IN 30 MIN", "AT 3PM", "NEXT MONDAY" — REMINDERS AUTO-SET
            </p>
          </form>
        )}

        {/* Filter tabs */}
        <div className="flex gap-px border-b border-border pb-px">
          {(["open", "done", "all"] as ViewFilter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`font-mono px-3 py-1.5 text-[9px] tracking-wider transition-colors border-b-2 -mb-px ${
                filter === f
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground/30 hover:text-muted-foreground/60"
              }`}
            >
              {f.toUpperCase()}
              {f === "open" && ` (${openCount})`}
              {f === "done" && ` (${doneCount})`}
            </button>
          ))}
        </div>

        {/* Task list */}
        {loading ? (
          <div className="flex justify-center py-12">
            <span className="font-mono text-[10px] text-muted-foreground/30 animate-pulse tracking-wider">
              LOADING...
            </span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-12">
            <p className="font-mono text-[10px] text-muted-foreground/30 tracking-wider">
              {filter === "open"
                ? "NO OPEN TASKS"
                : filter === "done"
                  ? "NO COMPLETED TASKS"
                  : "NO TASKS"}
            </p>
          </div>
        ) : (
          <div className="space-y-px">
            {filtered.map((task) => (
              <div
                key={task.id}
                className={`group flex items-start gap-3 px-4 py-3 hover:bg-card/60 transition-colors ${
                  editingId === task.id
                    ? "bg-card border border-border"
                    : ""
                }`}
              >
                {/* Checkbox */}
                <button
                  onClick={() => toggleDone(task)}
                  className={`w-3.5 h-3.5 border shrink-0 mt-0.5 flex items-center justify-center transition-colors cursor-pointer ${
                    task.done
                      ? "bg-success/20 border-success/30 hover:bg-success/30"
                      : "border-border hover:border-primary/40 hover:bg-primary/[0.06]"
                  }`}
                >
                  {task.done && (
                    <span className="w-1.5 h-1.5 bg-success/60" />
                  )}
                </button>

                {/* Content */}
                {editingId === task.id ? (
                  <div className="flex-1 space-y-2 min-w-0">
                    <input
                      ref={editInputRef}
                      type="text"
                      value={editText}
                      onChange={(e) => setEditText(e.target.value)}
                      onKeyDown={handleEditKeyDown}
                      className="w-full bg-transparent border border-border px-3 py-1.5 text-sm text-foreground outline-none focus:border-text-muted/30"
                    />
                    <div className="flex items-center gap-3 flex-wrap">
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-[8px] text-muted-foreground/30 tracking-wider">
                          PRI:
                        </span>
                        {[0, 1, 2].map((p) => (
                          <button
                            key={p}
                            type="button"
                            onClick={() => setEditPriority(p)}
                            className={`font-mono px-1.5 py-0.5 text-[8px] tracking-wider transition-colors ${
                              editPriority === p
                                ? p === 2
                                  ? "bg-destructive/10 text-destructive"
                                  : p === 1
                                    ? "bg-warning/10 text-warning"
                                    : "bg-input text-foreground"
                                : "text-muted-foreground/20 hover:text-muted-foreground/40"
                            }`}
                          >
                            {PRIORITY_LABELS[p]}
                          </button>
                        ))}
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="font-mono text-[8px] text-muted-foreground/30 tracking-wider">
                          DUE:
                        </span>
                        <input
                          type="text"
                          value={editDueDate}
                          onChange={(e) => setEditDueDate(e.target.value)}
                          onKeyDown={handleEditKeyDown}
                          placeholder="e.g. tomorrow at 3pm"
                          className="bg-transparent border border-border px-2 py-0.5 font-mono text-[10px] text-foreground placeholder:text-muted-foreground/20 outline-none w-44 focus:border-text-muted/30"
                        />
                      </div>
                    </div>
                    <div className="flex gap-2 pt-1">
                      <button
                        onClick={saveEdit}
                        className="font-mono px-3 py-1 text-[9px] tracking-wider bg-primary/[0.08] text-primary border border-primary/30 hover:bg-primary/[0.12] transition-colors"
                      >
                        SAVE
                      </button>
                      <button
                        onClick={cancelEdit}
                        className="font-mono px-3 py-1 text-[9px] tracking-wider text-muted-foreground/40 hover:text-muted-foreground transition-colors"
                      >
                        CANCEL
                      </button>
                      {editDueDate && (
                        <button
                          onClick={() => setEditDueDate("")}
                          className="font-mono px-2 py-1 text-[8px] tracking-wider text-muted-foreground/20 hover:text-destructive transition-colors"
                        >
                          CLEAR DUE
                        </button>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      {task.priority > 0 && (
                        <span
                          className={`w-1 h-1 shrink-0 ${PRIORITY_DOTS[task.priority]}`}
                          title={PRIORITY_LABELS[task.priority]}
                        />
                      )}
                      <span
                        className={`text-sm ${task.done ? "line-through text-muted-foreground/30" : "text-foreground/80"}`}
                      >
                        {task.text}
                      </span>
                    </div>
                    {task.dueDate && (
                      <p
                        className={`font-mono text-[9px] mt-0.5 tracking-wider ${
                          task.done
                            ? "text-muted-foreground/20"
                            : isOverdue(task.dueDate)
                              ? "text-destructive/70"
                              : "text-muted-foreground/30"
                        }`}
                      >
                        {task.done
                          ? new Date(task.dueDate).toLocaleDateString("en-US", {
                              month: "short",
                              day: "numeric",
                              hour: "numeric",
                              minute: "2-digit",
                            })
                          : formatDueDate(task.dueDate)}
                      </p>
                    )}
                    {task.done && task.completedAt && (
                      <p className="font-mono text-[8px] text-muted-foreground/15 mt-0.5 tracking-wider">
                        COMPLETED{" "}
                        {new Date(task.completedAt).toLocaleDateString(
                          "en-US",
                          { month: "short", day: "numeric" },
                        )}
                      </p>
                    )}
                  </div>
                )}

                {/* Actions */}
                {editingId !== task.id && (
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                    {!task.done && (
                      <button
                        onClick={() => startEdit(task)}
                        className="font-mono px-1.5 py-0.5 text-[8px] tracking-wider text-muted-foreground/30 hover:text-muted-foreground transition-colors"
                        title="Edit"
                      >
                        EDIT
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(task.id)}
                      className="font-mono px-1.5 py-0.5 text-[8px] tracking-wider text-muted-foreground/30 hover:text-destructive transition-colors"
                      title="Delete"
                    >
                      DEL
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
