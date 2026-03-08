import type { ActionContract } from "./types";

export const BUILTIN_ACTION_CONTRACTS: readonly ActionContract[] = [
  {
    capabilityId: "memory-core",
    name: "remember",
    summary: "Store long-term user details in memory.",
  },
  {
    capabilityId: "memory-core",
    name: "recall",
    summary: "Search memory snippets relevant to a query.",
  },
  {
    capabilityId: "memory-core",
    name: "read_memory",
    summary: "Read a specific memory file by path.",
  },
  {
    capabilityId: "memory-core",
    name: "write_memory",
    summary: "Overwrite a memory file with structured content.",
  },
  {
    capabilityId: "memory-core",
    name: "append_memory",
    summary: "Append incremental notes to a memory file.",
  },
  {
    capabilityId: "profile",
    name: "get_profile",
    summary: "Read the current user's profile details.",
  },
  {
    capabilityId: "memory-ops",
    name: "list_memories",
    summary: "Browse memory files across sections.",
  },
  {
    capabilityId: "memory-ops",
    name: "journal",
    summary: "Append an entry to today's journal file.",
  },
  {
    capabilityId: "memory-ops",
    name: "daily_log",
    summary:
      "Append a note to today's daily memory log for short-term context.",
  },
  {
    capabilityId: "tasks",
    name: "list_tasks",
    summary: "List open and completed tasks.",
  },
  {
    capabilityId: "tasks",
    name: "add_task",
    summary: "Create a task with optional parsed due date.",
  },
  {
    capabilityId: "tasks",
    name: "complete_task",
    summary: "Mark a task as done by id or text match.",
  },
  {
    capabilityId: "tasks",
    name: "update_task",
    summary: "Update text, priority, or due date for a task.",
  },
  {
    capabilityId: "tasks",
    name: "delete_task",
    summary: "Delete a task by id or text match.",
  },
  {
    capabilityId: "focus",
    name: "get_current_focus",
    summary: "Read current focus from memory.",
  },
  {
    capabilityId: "focus",
    name: "set_current_focus",
    summary: "Set or replace current focus.",
  },
  {
    capabilityId: "focus",
    name: "clear_current_focus",
    summary: "Clear current focus state.",
  },
  {
    capabilityId: "environment",
    name: "get_weather",
    summary: "Fetch current weather for a location.",
  },
  {
    capabilityId: "environment",
    name: "get_current_time",
    summary: "Return current date/time for a timezone.",
  },
];

export function getActionContracts(capabilityId: string): ActionContract[] {
  return BUILTIN_ACTION_CONTRACTS.filter(
    (contract) => contract.capabilityId === capabilityId,
  );
}
