import { useState, useEffect, useRef } from "react";
import { useAuth } from "../context/AuthContext";
import { api, type TaskItem } from "../lib/api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
  0: "Normal",
  1: "High",
  2: "Urgent",
};
const PRIORITY_DOTS: Record<number, string> = {
  0: "",
  1: "bg-amber-400",
  2: "bg-red-500",
};

type ViewFilter = "open" | "done" | "all";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function Tasks() {
  const { user } = useAuth();
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<ViewFilter>("open");

  // Create form
  const [newText, setNewText] = useState("");
  const [newPriority, setNewPriority] = useState(0);
  const [showCreate, setShowCreate] = useState(false);
  const createInputRef = useRef<HTMLInputElement>(null);

  // Edit state
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editText, setEditText] = useState("");
  const [editPriority, setEditPriority] = useState(0);
  const [editDueDate, setEditDueDate] = useState("");
  const editInputRef = useRef<HTMLInputElement>(null);

  // -------------------------------------------------------------------------
  // Data fetching
  // -------------------------------------------------------------------------

  useEffect(() => {
    if (!user?.id) return;
    setLoading(true);
    api.tasks
      .list(user.id)
      .then(setTasks)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [user?.id]);

  // -------------------------------------------------------------------------
  // Actions
  // -------------------------------------------------------------------------

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newText.trim() || !user?.id) return;
    const created = await api.tasks.create(
      user.id,
      newText.trim(),
      newPriority || undefined,
      undefined,
      newText.trim(), // dueDateRaw — server parses NLP time expressions
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

  // -------------------------------------------------------------------------
  // Filtering & sorting
  // -------------------------------------------------------------------------

  const filtered = tasks
    .filter((t) => {
      if (filter === "open") return !t.done;
      if (filter === "done") return t.done;
      return true;
    })
    .sort((a, b) => {
      // Open tasks: sort by priority desc, then due date asc, then created
      if (!a.done && !b.done) {
        if (b.priority !== a.priority) return b.priority - a.priority;
        if (a.dueDate && b.dueDate) return a.dueDate.localeCompare(b.dueDate);
        if (a.dueDate) return -1;
        if (b.dueDate) return 1;
        return (a.createdAt ?? "").localeCompare(b.createdAt ?? "");
      }
      // Done tasks: most recently completed first
      if (a.done && b.done)
        return (b.completedAt ?? "").localeCompare(a.completedAt ?? "");
      return a.done ? 1 : -1;
    });

  const openCount = tasks.filter((t) => !t.done).length;
  const doneCount = tasks.filter((t) => t.done).length;
  const overdueCount = tasks.filter(
    (t) => !t.done && t.dueDate && isOverdue(t.dueDate),
  ).length;

  // -------------------------------------------------------------------------
  // Render
  // -------------------------------------------------------------------------

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-2xl mx-auto px-6 py-10 space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-lg font-medium text-text">Tasks</h1>
            <p className="text-xs text-text-muted/50 mt-0.5">
              {openCount} open
              {overdueCount > 0 && (
                <span className="text-red-400 ml-1.5">
                  · {overdueCount} overdue
                </span>
              )}
              {doneCount > 0 && (
                <span className="ml-1.5">· {doneCount} done</span>
              )}
            </p>
          </div>
          <button
            onClick={() => {
              setShowCreate(!showCreate);
              if (!showCreate)
                setTimeout(() => createInputRef.current?.focus(), 50);
            }}
            className="px-3 py-1.5 rounded-lg text-xs bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
          >
            {showCreate ? "Cancel" : "+ New task"}
          </button>
        </div>

        {/* Create form */}
        {showCreate && (
          <form
            onSubmit={handleCreate}
            className="bg-bg-card border border-border rounded-xl p-4 space-y-3"
          >
            <input
              ref={createInputRef}
              type="text"
              value={newText}
              onChange={(e) => setNewText(e.target.value)}
              placeholder='E.g. "Buy groceries tomorrow at 5pm" — time is parsed automatically'
              className="w-full bg-transparent border border-border rounded-lg px-3 py-2 text-sm text-text placeholder:text-text-muted/25 outline-none focus:border-text-muted/30 transition-colors"
            />
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <span className="text-[11px] text-text-muted/50">
                  Priority:
                </span>
                {[0, 1, 2].map((p) => (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setNewPriority(p)}
                    className={`px-2 py-0.5 rounded text-[11px] transition-colors ${
                      newPriority === p
                        ? p === 2
                          ? "bg-red-500/20 text-red-400"
                          : p === 1
                            ? "bg-amber-400/20 text-amber-400"
                            : "bg-bg-input text-text"
                        : "text-text-muted/40 hover:text-text-muted"
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
                className="px-4 py-1.5 rounded-lg text-xs bg-primary text-white hover:opacity-90 transition-opacity disabled:opacity-30"
              >
                Add task
              </button>
            </div>
            <p className="text-[10px] text-text-muted/30">
              Tip: Include time like "in 30 min", "at 3pm", "next Monday" —
              reminders are set automatically.
            </p>
          </form>
        )}

        {/* Filter tabs */}
        <div className="flex gap-1 border-b border-border pb-px">
          {(["open", "done", "all"] as ViewFilter[]).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-3 py-1.5 text-xs capitalize transition-colors border-b-2 -mb-px ${
                filter === f
                  ? "border-primary text-text"
                  : "border-transparent text-text-muted/50 hover:text-text-muted"
              }`}
            >
              {f}
              {f === "open" && ` (${openCount})`}
              {f === "done" && ` (${doneCount})`}
            </button>
          ))}
        </div>

        {/* Task list */}
        {loading ? (
          <div className="flex justify-center py-12">
            <span className="text-xs text-text-muted/40 animate-pulse">
              Loading tasks...
            </span>
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-12">
            <p className="text-sm text-text-muted/40">
              {filter === "open"
                ? "No open tasks. Nice!"
                : filter === "done"
                  ? "No completed tasks yet."
                  : "No tasks yet."}
            </p>
          </div>
        ) : (
          <div className="space-y-1">
            {filtered.map((task) => (
              <div
                key={task.id}
                className={`group flex items-start gap-3 px-4 py-3 rounded-lg hover:bg-bg-card/60 transition-colors ${
                  editingId === task.id
                    ? "bg-bg-card border border-border"
                    : ""
                }`}
              >
                {/* Checkbox */}
                <button
                  onClick={() => toggleDone(task)}
                  className={`w-[18px] h-[18px] rounded-full border shrink-0 mt-0.5 flex items-center justify-center transition-colors cursor-pointer ${
                    task.done
                      ? "bg-success/20 border-success/30 hover:bg-success/30"
                      : "border-border hover:border-primary/60 hover:bg-primary/10"
                  }`}
                >
                  {task.done && (
                    <span className="w-2 h-2 rounded-full bg-success/60" />
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
                      className="w-full bg-transparent border border-border rounded-lg px-3 py-1.5 text-sm text-text outline-none focus:border-text-muted/30"
                    />
                    <div className="flex items-center gap-3 flex-wrap">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-text-muted/40">
                          Priority:
                        </span>
                        {[0, 1, 2].map((p) => (
                          <button
                            key={p}
                            type="button"
                            onClick={() => setEditPriority(p)}
                            className={`px-1.5 py-0.5 rounded text-[10px] transition-colors ${
                              editPriority === p
                                ? p === 2
                                  ? "bg-red-500/20 text-red-400"
                                  : p === 1
                                    ? "bg-amber-400/20 text-amber-400"
                                    : "bg-bg-input text-text"
                                : "text-text-muted/30 hover:text-text-muted/60"
                            }`}
                          >
                            {PRIORITY_LABELS[p]}
                          </button>
                        ))}
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-text-muted/40">
                          Due:
                        </span>
                        <input
                          type="text"
                          value={editDueDate}
                          onChange={(e) => setEditDueDate(e.target.value)}
                          onKeyDown={handleEditKeyDown}
                          placeholder="e.g. tomorrow at 3pm"
                          className="bg-transparent border border-border rounded px-2 py-0.5 text-[11px] text-text placeholder:text-text-muted/25 outline-none w-44 focus:border-text-muted/30"
                        />
                      </div>
                    </div>
                    <div className="flex gap-2 pt-1">
                      <button
                        onClick={saveEdit}
                        className="px-3 py-1 rounded text-[11px] bg-primary/10 text-primary hover:bg-primary/20 transition-colors"
                      >
                        Save
                      </button>
                      <button
                        onClick={cancelEdit}
                        className="px-3 py-1 rounded text-[11px] text-text-muted/50 hover:text-text-muted transition-colors"
                      >
                        Cancel
                      </button>
                      {editDueDate && (
                        <button
                          onClick={() => setEditDueDate("")}
                          className="px-2 py-1 rounded text-[10px] text-text-muted/30 hover:text-red-400 transition-colors"
                        >
                          Clear due date
                        </button>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-1.5">
                      {task.priority > 0 && (
                        <span
                          className={`w-1.5 h-1.5 rounded-full shrink-0 ${PRIORITY_DOTS[task.priority]}`}
                          title={PRIORITY_LABELS[task.priority]}
                        />
                      )}
                      <span
                        className={`text-sm ${task.done ? "line-through text-text-muted/40" : "text-text/80"}`}
                      >
                        {task.text}
                      </span>
                    </div>
                    {task.dueDate && (
                      <p
                        className={`text-[11px] mt-0.5 ${
                          task.done
                            ? "text-text-muted/30"
                            : isOverdue(task.dueDate)
                              ? "text-red-400/80"
                              : "text-text-muted/40"
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
                      <p className="text-[10px] text-text-muted/25 mt-0.5">
                        Completed{" "}
                        {new Date(task.completedAt).toLocaleDateString(
                          "en-US",
                          { month: "short", day: "numeric" },
                        )}
                      </p>
                    )}
                  </div>
                )}

                {/* Actions (visible on hover, hidden during edit) */}
                {editingId !== task.id && (
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                    {!task.done && (
                      <button
                        onClick={() => startEdit(task)}
                        className="px-1.5 py-0.5 rounded text-[10px] text-text-muted/40 hover:text-text-muted hover:bg-bg-card transition-colors"
                        title="Edit"
                      >
                        ✎
                      </button>
                    )}
                    <button
                      onClick={() => handleDelete(task.id)}
                      className="px-1.5 py-0.5 rounded text-[10px] text-text-muted/40 hover:text-red-400 hover:bg-red-500/10 transition-colors"
                      title="Delete"
                    >
                      ×
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
