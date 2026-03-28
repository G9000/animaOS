import { describe, test, expect, afterEach } from "bun:test";
import { executeTodoWrite, executeTodoRead, resetTodos } from "./todo";

afterEach(() => {
  resetTodos();
});

describe("todo_write", () => {
  test("accepts a valid todo list", () => {
    const result = executeTodoWrite({
      todos: [
        { content: "Fix the bug", status: "in_progress", activeForm: "Fixing the bug" },
        { content: "Write tests", status: "pending", activeForm: "Writing tests" },
      ],
    });
    expect(result.status).toBe("success");
    expect(result.result).toContain("1 in progress");
    expect(result.result).toContain("1 pending");
  });

  test("rejects missing content", () => {
    const result = executeTodoWrite({
      todos: [{ content: "", status: "pending", activeForm: "Doing something" }],
    });
    expect(result.status).toBe("error");
  });

  test("rejects invalid status", () => {
    const result = executeTodoWrite({
      todos: [{ content: "Fix it", status: "done" as any, activeForm: "Fixing" }],
    });
    expect(result.status).toBe("error");
    expect(result.result).toContain("status");
  });

  test("rejects non-array", () => {
    const result = executeTodoWrite({ todos: "not an array" as any });
    expect(result.status).toBe("error");
  });

  test("warns when multiple in_progress", () => {
    const result = executeTodoWrite({
      todos: [
        { content: "Task A", status: "in_progress", activeForm: "Doing A" },
        { content: "Task B", status: "in_progress", activeForm: "Doing B" },
      ],
    });
    expect(result.status).toBe("success");
    expect(result.result).toContain("Warning");
  });

  test("replaces previous todo list", () => {
    executeTodoWrite({
      todos: [{ content: "First", status: "pending", activeForm: "Starting" }],
    });
    executeTodoWrite({
      todos: [{ content: "Second", status: "completed", activeForm: "Done" }],
    });
    const read = executeTodoRead();
    expect(read.result).toContain("Second");
    expect(read.result).not.toContain("First");
  });
});

describe("todo_read", () => {
  test("returns empty message when no todos", () => {
    const result = executeTodoRead();
    expect(result.result).toContain("No todos");
  });

  test("shows formatted list with status icons", () => {
    executeTodoWrite({
      todos: [
        { content: "Done task", status: "completed", activeForm: "Completed" },
        { content: "Current task", status: "in_progress", activeForm: "Working" },
        { content: "Future task", status: "pending", activeForm: "Pending" },
      ],
    });
    const result = executeTodoRead();
    expect(result.result).toContain("✓");
    expect(result.result).toContain("▸");
    expect(result.result).toContain("○");
  });
});
