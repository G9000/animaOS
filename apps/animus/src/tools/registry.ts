// apps/animus/src/tools/registry.ts
import type { ToolSchema } from "../client/protocol";

export const ACTION_TOOL_SCHEMAS: ToolSchema[] = [
  {
    name: "bash",
    description: "Execute a shell command and return its output.",
    parameters: {
      type: "object",
      properties: {
        command: {
          type: "string",
          description: "The bash command to execute",
        },
        timeout: {
          type: "number",
          description: "Timeout in milliseconds (default: 120000)",
        },
      },
      required: ["command"],
    },
  },
  {
    name: "read_file",
    description: "Read a file and return its contents with line numbers.",
    parameters: {
      type: "object",
      properties: {
        file_path: {
          type: "string",
          description: "Absolute path to the file",
        },
        offset: {
          type: "number",
          description: "Line offset to start reading from",
        },
        limit: {
          type: "number",
          description: "Max lines to read (default: 2000)",
        },
      },
      required: ["file_path"],
    },
  },
  {
    name: "write_file",
    description: "Write content to a file, creating directories as needed.",
    parameters: {
      type: "object",
      properties: {
        file_path: {
          type: "string",
          description: "Absolute path to the file",
        },
        content: { type: "string", description: "Content to write" },
      },
      required: ["file_path", "content"],
    },
  },
  {
    name: "edit_file",
    description: "Edit a file by replacing old_string with new_string.",
    parameters: {
      type: "object",
      properties: {
        file_path: {
          type: "string",
          description: "Absolute path to the file",
        },
        old_string: {
          type: "string",
          description: "Exact string to find and replace",
        },
        new_string: {
          type: "string",
          description: "Replacement string",
        },
      },
      required: ["file_path", "old_string", "new_string"],
    },
  },
  {
    name: "grep",
    description: "Search for a regex pattern across files.",
    parameters: {
      type: "object",
      properties: {
        pattern: {
          type: "string",
          description: "Regex pattern to search for",
        },
        path: {
          type: "string",
          description: "Directory to search in (default: cwd)",
        },
        include: {
          type: "string",
          description: "Glob to filter files (e.g. '*.ts')",
        },
      },
      required: ["pattern"],
    },
  },
  {
    name: "glob",
    description: "Find files matching a glob pattern.",
    parameters: {
      type: "object",
      properties: {
        pattern: {
          type: "string",
          description: "Glob pattern (e.g. '**/*.ts')",
        },
        path: {
          type: "string",
          description: "Base directory (default: cwd)",
        },
      },
      required: ["pattern"],
    },
  },
  {
    name: "list_dir",
    description: "List contents of a directory.",
    parameters: {
      type: "object",
      properties: {
        path: { type: "string", description: "Directory path to list" },
      },
      required: ["path"],
    },
  },
  {
    name: "multi_edit",
    description:
      "Apply multiple edits to a single file atomically. All edits are validated first; if any old_string is missing the whole batch is rejected.",
    parameters: {
      type: "object",
      properties: {
        file_path: {
          type: "string",
          description: "Absolute path to the file",
        },
        edits: {
          type: "array",
          description: "Array of {old_string, new_string} replacements",
          items: {
            type: "object",
            properties: {
              old_string: {
                type: "string",
                description: "Exact string to find",
              },
              new_string: {
                type: "string",
                description: "Replacement string",
              },
            },
            required: ["old_string", "new_string"],
          },
        },
      },
      required: ["file_path", "edits"],
    },
  },
  {
    name: "ask_user",
    description: "Ask the user a question and wait for their response.",
    parameters: {
      type: "object",
      properties: {
        question: {
          type: "string",
          description: "Question to ask the user",
        },
      },
      required: ["question"],
    },
  },
  // ── New tools ──────────────────────────────────────────

  {
    name: "todo_write",
    description:
      "Create or update a structured task list for tracking multi-step work. Each todo has content (imperative), status (pending|in_progress|completed), and activeForm (present continuous). Keep exactly one todo in_progress at a time.",
    parameters: {
      type: "object",
      properties: {
        todos: {
          type: "array",
          description: "The full todo list (replaces previous list)",
          items: {
            type: "object",
            properties: {
              content: { type: "string", description: "What to do (imperative form)" },
              status: { type: "string", enum: ["pending", "in_progress", "completed"] },
              activeForm: { type: "string", description: "Present continuous form, e.g. 'Running tests'" },
            },
            required: ["content", "status", "activeForm"],
          },
        },
      },
      required: ["todos"],
    },
  },
  {
    name: "todo_read",
    description: "Read the current todo list to check progress.",
    parameters: {
      type: "object",
      properties: {},
    },
  },
  {
    name: "bg_start",
    description:
      "Start a command in the background (dev servers, watchers, builds). Returns a process ID for reading output or stopping later.",
    parameters: {
      type: "object",
      properties: {
        command: {
          type: "string",
          description: "The bash command to run in the background",
        },
        cwd: {
          type: "string",
          description: "Working directory (default: cwd)",
        },
      },
      required: ["command"],
    },
  },
  {
    name: "bg_output",
    description:
      "Read output from a background process. By default returns only new lines since last read.",
    parameters: {
      type: "object",
      properties: {
        id: {
          type: "string",
          description: "Process ID from bg_start",
        },
        all: {
          type: "boolean",
          description: "If true, return all output instead of just new lines",
        },
      },
      required: ["id"],
    },
  },
  {
    name: "bg_stop",
    description: "Kill a background process and remove it from the process list.",
    parameters: {
      type: "object",
      properties: {
        id: {
          type: "string",
          description: "Process ID from bg_start",
        },
      },
      required: ["id"],
    },
  },
  {
    name: "bg_list",
    description: "List all background processes with their status and uptime.",
    parameters: {
      type: "object",
      properties: {},
    },
  },
];
