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
];
