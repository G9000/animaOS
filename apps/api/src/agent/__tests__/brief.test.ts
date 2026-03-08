import { beforeEach, describe, expect, mock, test } from "bun:test";

type TaskRow = { text: string; done: boolean; dueDate: string | null };
type MessageRow = { content: string };
type LastMessageRow = { createdAt: string | null };

const state: {
  focusContent: string | null;
  factsContent: string | null;
  tasksRows: TaskRow[];
  recentRows: MessageRow[];
  lastRows: LastMessageRow[];
} = {
  focusContent: null,
  factsContent: null,
  tasksRows: [],
  recentRows: [],
  lastRows: [],
};

let selectCallIndex = 0;
let invokeMode: "ok" | "throw" = "ok";
let invokeText = "hello";

const readMemoryMock = mock(async (_scope: string, _userId: number, key: string) => {
  if (key === "current-focus") {
    if (!state.focusContent) throw new Error("missing focus");
    return { content: state.focusContent };
  }
  if (key === "facts") {
    if (!state.factsContent) throw new Error("missing facts");
    return { content: state.factsContent };
  }
  throw new Error(`unknown memory key: ${key}`);
});

const dbSelectMock = mock(() => {
  const call = selectCallIndex++;
  return {
    from: () => ({
      where: () => {
        if (call === 0) {
          return Promise.resolve(state.tasksRows);
        }

        return {
          orderBy: () => ({
            limit: () => Promise.resolve(call === 1 ? state.recentRows : state.lastRows),
          }),
        };
      },
    }),
  };
});

const invokeMock = mock(async () => {
  if (invokeMode === "throw") {
    throw new Error("model down");
  }
  return { content: invokeText };
});

const createModelMock = mock(() => ({ invoke: invokeMock }));
const getAgentConfigMock = mock(async () => ({ provider: "openai", model: "gpt-4o-mini" }));
const getSoulPromptForUserMock = mock(async () => "SOUL");
const renderPromptTemplateMock = mock(() => "SYSTEM_PROMPT");

mock.module("../../db", () => ({
  db: {
    select: dbSelectMock,
  },
}));

mock.module("../../memory", () => ({
  readMemory: readMemoryMock,
}));

mock.module("../models", () => ({
  createModel: createModelMock,
}));

mock.module("../config", () => ({
  getAgentConfig: getAgentConfigMock,
}));

mock.module("../prompt", () => ({
  getSoulPromptForUser: getSoulPromptForUserMock,
  renderPromptTemplate: renderPromptTemplateMock,
}));

const { generateBrief } = await import("../brief");

function resetState() {
  state.focusContent = null;
  state.factsContent = null;
  state.tasksRows = [];
  state.recentRows = [];
  state.lastRows = [];
  selectCallIndex = 0;
  invokeMode = "ok";
  invokeText = "hello";

  readMemoryMock.mockClear();
  dbSelectMock.mockClear();
  invokeMock.mockClear();
  createModelMock.mockClear();
  getAgentConfigMock.mockClear();
  getSoulPromptForUserMock.mockClear();
  renderPromptTemplateMock.mockClear();
}

beforeEach(() => {
  resetState();
});

describe("generateBrief", () => {
  test("returns static greeting when no context is available", async () => {
    const result = await generateBrief(101);

    expect(result.message).toBe("How was today?");
    expect(result.context).toEqual({
      currentFocus: null,
      openTaskCount: 0,
      daysSinceLastChat: null,
    });
    expect(createModelMock).toHaveBeenCalledTimes(0);
    expect(invokeMock).toHaveBeenCalledTimes(0);
  });

  test("generates brief with model and filtered open task count", async () => {
    state.focusContent = "- [ ] Ship dashboard refactor";
    state.factsContent = "Prefers concise replies.";
    state.tasksRows = [
      { text: "Future task", done: false, dueDate: "2099-01-01T10:00:00" },
      { text: "No due date", done: false, dueDate: null },
      { text: "Past due", done: false, dueDate: "2000-01-01T00:00:00" },
      { text: "Done task", done: true, dueDate: "2099-01-01T12:00:00" },
    ];
    state.recentRows = [
      { content: "Need to plan my week and prioritize what matters first." },
    ];
    state.lastRows = [{ createdAt: new Date().toISOString() }];
    invokeText = "  You have momentum today. Keep going.  ";

    const result = await generateBrief(102);

    expect(createModelMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(result.message).toBe("You have momentum today. Keep going.");
    expect(result.context.currentFocus).toBe("Ship dashboard refactor");
    expect(result.context.openTaskCount).toBe(2);
    expect(renderPromptTemplateMock).toHaveBeenCalledTimes(1);
  });

  test("returns fallback message when model invocation fails", async () => {
    state.focusContent = "- [ ] Prepare launch plan";
    state.tasksRows = [{ text: "Prepare launch plan", done: false, dueDate: null }];
    state.lastRows = [{ createdAt: new Date().toISOString() }];
    invokeMode = "throw";

    const result = await generateBrief(103);

    expect(createModelMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledTimes(1);
    expect(result.message).toBe('Still working on "Prepare launch plan"?');
    expect(result.context.openTaskCount).toBe(1);
  });

  test("uses cache for repeated calls on same day and user", async () => {
    state.focusContent = "- [ ] Finish docs";
    state.tasksRows = [{ text: "Finish docs", done: false, dueDate: null }];
    state.lastRows = [{ createdAt: new Date().toISOString() }];
    invokeText = "First brief";

    const first = await generateBrief(104);

    invokeMode = "throw";
    const second = await generateBrief(104);

    expect(first.message).toBe("First brief");
    expect(second.message).toBe("First brief");
    expect(createModelMock).toHaveBeenCalledTimes(1);
    expect(invokeMock).toHaveBeenCalledTimes(1);
  });
});
