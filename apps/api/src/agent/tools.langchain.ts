// Agent tools — LangChain @tool format for LangGraph
// Memory is now stored as markdown files in /memory at the project root.

import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { eq } from "drizzle-orm";
import { db } from "../db";
import * as schema from "../db/schema";

import {
  writeMemory,
  appendMemory,
  readMemory,
  deleteMemory,
  searchMemories,
  listMemories as listMemoryFiles,
  listAllMemories,
  writeJournalEntry,
  type MemorySection,
} from "../memory";

// Each tool receives userId via closure when created.
// This keeps the LangChain tool interface clean (no userId param exposed to the LLM).

// Map memory categories to filesystem sections
function categoryToSection(category: string): MemorySection {
  switch (category) {
    case "fact":
    case "preference":
    case "goal":
      return "user";
    case "relationship":
      return "relationships";
    case "note":
      return "knowledge";
    default:
      return "knowledge";
  }
}

// Derive a reasonable filename from content
function contentToFilename(content: string, category: string): string {
  // Extract first meaningful words for filename
  const words = content
    .replace(/[^a-zA-Z0-9\s]/g, "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 4)
    .join("-")
    .toLowerCase();

  return words || category;
}

interface ParsedTask {
  id: number;
  text: string;
  done: boolean;
  lineIndex: number;
  indent: string;
}

function parseTasksFromGoals(content: string): ParsedTask[] {
  const lines = content.split(/\r?\n/);
  const tasks: ParsedTask[] = [];

  lines.forEach((line, lineIndex) => {
    const checklist = line.match(/^(\s*)- \[( |x|X)\]\s+(.+)$/);
    if (checklist) {
      tasks.push({
        id: tasks.length + 1,
        text: checklist[3].trim(),
        done: checklist[2].toLowerCase() === "x",
        lineIndex,
        indent: checklist[1] || "",
      });
      return;
    }

    const bullet = line.match(/^(\s*)- (.+)$/);
    if (bullet) {
      tasks.push({
        id: tasks.length + 1,
        text: bullet[2].trim(),
        done: false,
        lineIndex,
        indent: bullet[1] || "",
      });
    }
  });

  return tasks;
}

async function loadGoalsFile(userId: number) {
  const existing = await readMemory("user", userId, "goals").catch(() => null);
  return existing;
}

async function saveGoalsFile(userId: number, content: string) {
  await writeMemory("user", userId, "goals", content, {
    category: "goal",
    tags: ["goal", "task"],
    source: "conversation",
  });
}

function parseCurrentFocus(content: string): string {
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line === "---" || line.startsWith("#")) continue;

    const checklist = line.match(/^- \[(?: |x|X)\]\s+(.+)$/);
    if (checklist) return checklist[1].trim();

    const bullet = line.match(/^- (.+)$/);
    if (bullet) return bullet[1].trim();

    return line;
  }

  return "";
}

function renderCurrentFocus(focus: string, note?: string): string {
  const lines = ["# Current Focus", "", `- [ ] ${focus.trim()}`];

  if (note?.trim()) {
    lines.push("", "## Note", note.trim());
  }

  return lines.join("\n");
}

export function createTools(userId: number) {
  const remember = tool(
    async ({ content, category }) => {
      try {
        const section = categoryToSection(category);

        // For user-level categories, group into a single file per category
        // For relationships/knowledge, create per-topic files
        let filename: string;
        if (section === "user") {
          // Append to a category file (e.g., preferences.md, goals.md, facts.md)
          filename = category === "fact" ? "facts" : `${category}s`;
          const entry = `- ${content}`;
          await appendMemory(section, userId, filename, entry, {
            category,
            tags: [category],
            source: "conversation",
          });
        } else {
          // For relationships/knowledge, derive filename from content
          filename = contentToFilename(content, category);
          await appendMemory(section, userId, filename, `- ${content}`, {
            category,
            tags: [category],
            source: "conversation",
          });
        }

        return JSON.stringify({
          status: "stored",
          content,
          category,
          file: `${section}/${userId}/${filename}.md`,
        });
      } catch (err) {
        console.error("[tool:remember] Failed to store memory:", err);
        return JSON.stringify({
          status: "error",
          message: `Failed to store memory: ${(err as Error).message}`,
        });
      }
    },
    {
      name: "remember",
      description:
        "Store a piece of information about the user for long-term memory. Use this when the user shares facts, preferences, goals, or important details about themselves. Information is saved to markdown files organized by category.",
      schema: z.object({
        content: z.string().describe("The information to remember"),
        category: z
          .enum(["fact", "preference", "goal", "relationship", "note"])
          .describe("The category of memory"),
      }),
    },
  );

  const recall = tool(
    async ({ query }) => {
      const results = await searchMemories(userId, query);

      if (results.length === 0) {
        return JSON.stringify({
          results: [],
          message: "No matching memories found",
        });
      }

      return JSON.stringify({
        results: results.map((m) => ({
          file: m.path,
          content: m.snippet,
          category: m.meta.category,
          tags: m.meta.tags,
          updated: m.meta.updated,
        })),
      });
    },
    {
      name: "recall",
      description:
        "Search long-term memory for information about the user. Searches across all memory files (user profile, knowledge, relationships, journal) for matching content.",
      schema: z.object({
        query: z.string().describe("Search query to find relevant memories"),
      }),
    },
  );

  const readMemoryFile = tool(
    async ({ file }) => {
      try {
        // Parse section and filename from path like "user/preferences" or "knowledge/topic-name"
        const parts = file.split("/");
        const section = parts[0] as MemorySection;
        const filename = parts.slice(1).join("/") || parts[0];

        const result = await readMemory(section, userId, filename);
        return JSON.stringify({
          path: result.path,
          content: result.content,
          meta: result.meta,
        });
      } catch {
        return JSON.stringify({ error: `Memory file not found: ${file}` });
      }
    },
    {
      name: "read_memory",
      description:
        'Read the full contents of a specific memory file. Use paths like "user/preferences", "knowledge/topic", "relationships/person-name", "journal/2026-02-13".',
      schema: z.object({
        file: z
          .string()
          .describe(
            'Path to the memory file, e.g. "user/preferences" or "knowledge/coding"',
          ),
      }),
    },
  );

  const writeMemoryFile = tool(
    async ({ file, content, tags }) => {
      const parts = file.split("/");
      const section = (parts[0] || "knowledge") as MemorySection;
      const filename = parts.slice(1).join("/") || parts[0];

      const result = await writeMemory(section, userId, filename, content, {
        category: section,
        tags: tags || [],
        source: "conversation",
      });

      return JSON.stringify({
        status: "written",
        path: result.path,
      });
    },
    {
      name: "write_memory",
      description:
        'Write or overwrite a memory file with new content. Use this for structured knowledge that replaces the previous version. Path format: "section/filename" (e.g., "user/preferences", "knowledge/coding-style").',
      schema: z.object({
        file: z
          .string()
          .describe(
            'Path to the memory file, e.g. "user/preferences" or "knowledge/project-notes"',
          ),
        content: z.string().describe("The markdown content to write"),
        tags: z
          .array(z.string())
          .optional()
          .describe("Optional tags for the memory"),
      }),
    },
  );

  const getProfile = tool(
    async () => {
      const [user] = await db
        .select({
          name: schema.users.name,
          gender: schema.users.gender,
          age: schema.users.age,
          birthday: schema.users.birthday,
          username: schema.users.username,
        })
        .from(schema.users)
        .where(eq(schema.users.id, userId));

      if (!user) return JSON.stringify({ error: "User not found" });
      return JSON.stringify(user);
    },
    {
      name: "get_profile",
      description:
        "Get the user's profile information including name, age, gender, birthday.",
      schema: z.object({}),
    },
  );

  const browseMemories = tool(
    async ({ section }) => {
      const results = section
        ? await listMemoryFiles(section as MemorySection, userId)
        : await listAllMemories(userId);

      return JSON.stringify({
        count: results.length,
        memories: results.map((m) => ({
          path: m.path,
          category: m.meta.category,
          tags: m.meta.tags,
          updated: m.meta.updated,
          snippet: m.snippet,
        })),
      });
    },
    {
      name: "list_memories",
      description:
        "List all stored memory files, optionally filtered by section (user, knowledge, relationships, journal).",
      schema: z.object({
        section: z
          .enum(["user", "knowledge", "relationships", "journal"])
          .optional()
          .describe("Optional section filter"),
      }),
    },
  );

  const journal = tool(
    async ({ entry }) => {
      const result = await writeJournalEntry(userId, entry);
      return JSON.stringify({
        status: "journaled",
        path: result.path,
      });
    },
    {
      name: "journal",
      description:
        "Write a journal entry for today. Use this to log important events, session summaries, or daily notes. Content is appended to today's journal file.",
      schema: z.object({
        entry: z.string().describe("The journal entry content to append"),
      }),
    },
  );

  const listTasks = tool(
    async () => {
      const goals = await loadGoalsFile(userId);
      if (!goals) {
        return JSON.stringify({
          count: 0,
          open: 0,
          completed: 0,
          tasks: [],
          message: "No goals/tasks file exists yet.",
        });
      }

      const tasks = parseTasksFromGoals(goals.content);
      const open = tasks.filter((t) => !t.done).length;
      const completed = tasks.filter((t) => t.done).length;

      return JSON.stringify({
        count: tasks.length,
        open,
        completed,
        tasks: tasks.map((task) => ({
          id: task.id,
          text: task.text,
          done: task.done,
        })),
      });
    },
    {
      name: "list_tasks",
      description:
        "List user tasks from user/goals memory file. Returns task ids and completion status.",
      schema: z.object({}),
    },
  );

  const addTask = tool(
    async ({ task }) => {
      const text = task.trim();
      if (!text) {
        return JSON.stringify({ status: "error", error: "Task text is required" });
      }

      const goals = await loadGoalsFile(userId);
      let content = goals?.content ?? "";

      if (!content.trim()) {
        content = `# User Goals\n\n- [ ] ${text}`;
      } else {
        const lines = content.split(/\r?\n/);
        if (lines[lines.length - 1]?.trim() !== "") {
          lines.push("");
        }
        lines.push(`- [ ] ${text}`);
        content = lines.join("\n");
      }

      await saveGoalsFile(userId, content);

      const tasks = parseTasksFromGoals(content);
      const added = tasks[tasks.length - 1];

      return JSON.stringify({
        status: "added",
        task: {
          id: added?.id,
          text,
          done: false,
        },
      });
    },
    {
      name: "add_task",
      description:
        "Add a new open task to user/goals memory file as a checklist item.",
      schema: z.object({
        task: z.string().min(1).describe("Task text to add"),
      }),
    },
  );

  const completeTask = tool(
    async ({ id, task }) => {
      const goals = await loadGoalsFile(userId);
      if (!goals) {
        return JSON.stringify({
          status: "error",
          error: "No goals/tasks file found",
        });
      }

      const lines = goals.content.split(/\r?\n/);
      const tasks = parseTasksFromGoals(goals.content);

      if (tasks.length === 0) {
        return JSON.stringify({
          status: "error",
          error: "No tasks available to complete",
        });
      }

      let target: ParsedTask | undefined;

      if (typeof id === "number") {
        target = tasks.find((t) => t.id === id);
      } else if (typeof task === "string" && task.trim()) {
        const needle = task.trim().toLowerCase();
        target =
          tasks.find((t) => t.text.toLowerCase() === needle) ||
          tasks.find((t) => t.text.toLowerCase().includes(needle));
      }

      if (!target) {
        return JSON.stringify({
          status: "error",
          error: "Task not found",
        });
      }

      lines[target.lineIndex] = `${target.indent}- [x] ${target.text}`;
      const updatedContent = lines.join("\n");
      await saveGoalsFile(userId, updatedContent);

      return JSON.stringify({
        status: "completed",
        task: {
          id: target.id,
          text: target.text,
        },
      });
    },
    {
      name: "complete_task",
      description:
        "Mark a task as completed in user/goals memory. Provide either task id or task text.",
      schema: z
        .object({
          id: z.number().int().positive().optional(),
          task: z.string().optional(),
        })
        .refine((v) => v.id !== undefined || (v.task?.trim().length ?? 0) > 0, {
          message: "Provide id or task",
        }),
    },
  );

  const getCurrentFocus = tool(
    async () => {
      const existing = await readMemory("user", userId, "current-focus").catch(
        () => null,
      );

      if (!existing) {
        return JSON.stringify({
          status: "empty",
          focus: null,
          message: "No current focus set",
        });
      }

      const focus = parseCurrentFocus(existing.content);

      return JSON.stringify({
        status: "ok",
        focus: focus || null,
        path: existing.path,
        updated: existing.meta.updated,
      });
    },
    {
      name: "get_current_focus",
      description:
        "Get the user's current focus item from user/current-focus memory file.",
      schema: z.object({}),
    },
  );

  const setCurrentFocus = tool(
    async ({ focus, note }) => {
      const focusText = focus.trim();
      if (!focusText) {
        return JSON.stringify({
          status: "error",
          error: "Focus text is required",
        });
      }

      const content = renderCurrentFocus(focusText, note);
      const result = await writeMemory("user", userId, "current-focus", content, {
        category: "goal",
        tags: ["focus", "task"],
        source: "conversation",
      });

      return JSON.stringify({
        status: "set",
        focus: focusText,
        path: result.path,
      });
    },
    {
      name: "set_current_focus",
      description:
        "Set or replace the user's current focus item in user/current-focus memory.",
      schema: z.object({
        focus: z.string().min(1).describe("Current focus text"),
        note: z.string().optional().describe("Optional note for the focus"),
      }),
    },
  );

  const clearCurrentFocus = tool(
    async () => {
      const deleted = await deleteMemory("user", userId, "current-focus");

      return JSON.stringify({
        status: deleted ? "cleared" : "not_found",
      });
    },
    {
      name: "clear_current_focus",
      description: "Clear current focus by deleting user/current-focus memory file.",
      schema: z.object({}),
    },
  );

  const getWeather = tool(
    async ({ location }) => {
      try {
        // Open-Meteo — free, no API key needed
        // First geocode the location
        const geoRes = await fetch(
          `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(location)}&count=1`,
        );
        const geoData = (await geoRes.json()) as {
          results?: Array<{
            latitude: number;
            longitude: number;
            name: string;
            country: string;
          }>;
        };

        if (!geoData.results?.length) {
          return JSON.stringify({ error: `Location not found: ${location}` });
        }

        const { latitude, longitude, name, country } = geoData.results[0];

        // Fetch current weather
        const weatherRes = await fetch(
          `https://api.open-meteo.com/v1/forecast?latitude=${latitude}&longitude=${longitude}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m&temperature_unit=celsius`,
        );
        const weatherData = (await weatherRes.json()) as {
          current: {
            temperature_2m: number;
            relative_humidity_2m: number;
            apparent_temperature: number;
            weather_code: number;
            wind_speed_10m: number;
          };
        };

        const c = weatherData.current;
        return JSON.stringify({
          location: `${name}, ${country}`,
          temperature: `${c.temperature_2m}°C`,
          feels_like: `${c.apparent_temperature}°C`,
          humidity: `${c.relative_humidity_2m}%`,
          wind: `${c.wind_speed_10m} km/h`,
          condition: weatherCodeToText(c.weather_code),
        });
      } catch (err) {
        return JSON.stringify({
          error: `Weather fetch failed: ${(err as Error).message}`,
        });
      }
    },
    {
      name: "get_weather",
      description:
        "Get current weather data for a location. Returns temperature, humidity, wind speed, and conditions.",
      schema: z.object({
        location: z
          .string()
          .describe("City name or location to get weather for"),
      }),
    },
  );

  const getCurrentTime = tool(
    async ({ timeZone, locale }) => {
      const now = new Date();
      const zone = timeZone || Intl.DateTimeFormat().resolvedOptions().timeZone;

      try {
        const formatted = new Intl.DateTimeFormat(locale || "en-US", {
          timeZone: zone,
          dateStyle: "full",
          timeStyle: "long",
          hour12: false,
        }).format(now);

        return JSON.stringify({
          status: "ok",
          iso: now.toISOString(),
          unix: Math.floor(now.getTime() / 1000),
          timeZone: zone,
          localTime: formatted,
        });
      } catch {
        return JSON.stringify({
          status: "error",
          error: `Invalid timezone: ${timeZone}`,
        });
      }
    },
    {
      name: "get_current_time",
      description:
        "Get the current date and time for a user timezone. Provide an IANA timezone like 'America/New_York'.",
      schema: z.object({
        timeZone: z
          .string()
          .optional()
          .describe("Optional IANA timezone, e.g. America/New_York"),
        locale: z
          .string()
          .optional()
          .describe("Optional locale, e.g. en-US"),
      }),
    },
  );

  return [
    remember,
    recall,
    readMemoryFile,
    writeMemoryFile,
    getProfile,
    browseMemories,
    journal,
    listTasks,
    addTask,
    completeTask,
    getCurrentFocus,
    setCurrentFocus,
    clearCurrentFocus,
    getWeather,
    getCurrentTime,
  ];
}

// WMO weather codes → readable text
function weatherCodeToText(code: number): string {
  const map: Record<number, string> = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",
    80: "Slight showers",
    81: "Moderate showers",
    82: "Violent showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
  };
  return map[code] || `Code ${code}`;
}
