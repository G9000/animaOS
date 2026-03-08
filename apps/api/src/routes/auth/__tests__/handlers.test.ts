import { beforeEach, describe, expect, mock, test } from "bun:test";
import type { Context } from "hono";

const eqMock = mock(() => ({}));

type UserRow = {
  id: number;
  username: string;
  name: string;
  password?: string;
  createdAt?: string;
};

type UserKeyRow = {
  userId: number;
  kdfSalt: string;
  kdfTimeCost: number;
  kdfMemoryCostKib: number;
  kdfParallelism: number;
  kdfKeyLength: number;
  wrapIv: string;
  wrapTag: string;
  wrappedDek: string;
};

const users = {
  id: "users.id",
  username: "users.username",
  name: "users.name",
  password: "users.password",
  createdAt: "users.createdAt",
};

const userKeys = {
  userId: "userKeys.userId",
};

const selectQueue: Array<unknown[]> = [];
let insertedUserRow: UserRow = {
  id: 1,
  username: "alice",
  name: "Alice",
  createdAt: "2026-03-08T00:00:00.000Z",
};
const insertCalls: Array<{ table: string; payload: unknown }> = [];

const dbSelectMock = mock(() => ({
  from: () => ({
    where: () => Promise.resolve(selectQueue.shift() || []),
  }),
}));

const dbInsertMock = mock((table: unknown) => {
  const tableName = table === users ? "users" : "user_keys";
  return {
    values: (payload: unknown) => {
      insertCalls.push({ table: tableName, payload });
      if (table === users) {
        return {
          returning: () => Promise.resolve([insertedUserRow]),
        };
      }
      return Promise.resolve(undefined);
    },
  };
});

const createWrappedDekMock = mock(() => ({
  dek: Buffer.alloc(32, 7),
  record: {
    kdfSalt: "salt",
    kdfTimeCost: 3,
    kdfMemoryCostKib: 65536,
    kdfParallelism: 1,
    kdfKeyLength: 32,
    wrapIv: "iv",
    wrapTag: "tag",
    wrappedDek: "wrapped",
  },
}));

const unwrapDekMock = mock(() => Buffer.alloc(32, 9));
const createUnlockSessionMock = mock(() => "unlock-token");
const revokeUnlockSessionMock = mock(() => {});
const resolveUnlockSessionMock = mock(() => null);
const readUnlockTokenMock = mock(() => undefined);
const ensureDefaultSoulMock = mock(async () => {});
const readMemoryMock = mock(async (): Promise<{ content: string }> => {
  throw new Error("missing");
});
const writeMemoryMock = mock(async () => ({}));

mock.module("drizzle-orm", () => ({ eq: eqMock }));

mock.module("../../../db", () => ({
  db: {
    select: dbSelectMock,
    insert: dbInsertMock,
  },
}));

mock.module("../../../db/schema", () => ({
  users,
  userKeys,
}));

mock.module("../../../lib/auth-crypto", () => ({
  createWrappedDek: createWrappedDekMock,
  unwrapDek: unwrapDekMock,
}));

mock.module("../../../lib/unlock-session", () => ({
  createUnlockSession: createUnlockSessionMock,
  revokeUnlockSession: revokeUnlockSessionMock,
  resolveUnlockSession: resolveUnlockSessionMock,
}));

mock.module("../../../lib/require-unlock", () => ({
  readUnlockToken: readUnlockTokenMock,
}));

mock.module("../../../lib/user-soul", () => ({
  ensureDefaultSoul: ensureDefaultSoulMock,
}));

mock.module("../../../memory", () => ({
  readMemory: readMemoryMock,
  writeMemory: writeMemoryMock,
  appendMemory: mock(async () => ({})),
  deleteMemory: mock(async () => true),
  searchMemories: mock(async () => []),
  listMemories: mock(async () => []),
  listAllMemories: mock(async () => []),
  listSections: mock(async () => []),
  writeJournalEntry: mock(async () => ({})),
  loadFullMemoryContext: mock(async () => ""),
  readMemoryByPath: mock(async () => ({ meta: {}, content: "", path: "" })),
  MEMORY_ROOT: "",
  SECTIONS: ["user", "knowledge", "relationships", "journal"],
}));

const { register, login } = await import("../handlers");

function createContext(body: unknown): Context {
  return {
    req: {
      valid: () => body,
      header: () => undefined,
    },
    json: (payload: unknown, status = 200) =>
      new Response(JSON.stringify(payload), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
  } as unknown as Context;
}

beforeEach(() => {
  selectQueue.length = 0;
  insertCalls.length = 0;

  insertedUserRow = {
    id: 1,
    username: "alice",
    name: "Alice",
    createdAt: "2026-03-08T00:00:00.000Z",
  };

  eqMock.mockClear();
  dbSelectMock.mockClear();
  dbInsertMock.mockClear();
  createWrappedDekMock.mockClear();
  unwrapDekMock.mockClear();
  createUnlockSessionMock.mockClear();
  revokeUnlockSessionMock.mockClear();
  resolveUnlockSessionMock.mockClear();
  readUnlockTokenMock.mockClear();
  ensureDefaultSoulMock.mockClear();
  readMemoryMock.mockClear();
  writeMemoryMock.mockClear();

  readMemoryMock.mockImplementation(async () => {
    throw new Error("missing");
  });
  unwrapDekMock.mockImplementation(() => Buffer.alloc(32, 9));
});

describe("auth handlers", () => {
  test("register creates user key material and seeds defaults", async () => {
    selectQueue.push([]); // username availability check
    insertedUserRow = {
      id: 42,
      username: "alice",
      name: "Alice",
      createdAt: "2026-03-08T00:00:00.000Z",
    };

    const response = await register(
      createContext({
        username: "Alice",
        password: "pw123",
        name: "Alice",
        personaTemplate: "alice",
      }),
    );

    expect(response.status).toBe(201);
    const payload = (await response.json()) as { unlockToken: string; username: string };

    expect(payload.unlockToken).toBe("unlock-token");
    expect(payload.username).toBe("alice");

    expect(createWrappedDekMock).toHaveBeenCalledTimes(1);
    expect(createUnlockSessionMock).toHaveBeenCalledTimes(1);
    expect(createUnlockSessionMock).toHaveBeenCalledWith(42, expect.any(Buffer));

    expect(insertCalls.length).toBe(2);
    expect(insertCalls[0].table).toBe("users");
    expect((insertCalls[0].payload as { username: string }).username).toBe("alice");
    expect(insertCalls[1].table).toBe("user_keys");
    expect((insertCalls[1].payload as { userId: number }).userId).toBe(42);

    expect(ensureDefaultSoulMock).toHaveBeenCalledWith(42, "alice");
    expect(writeMemoryMock).toHaveBeenCalledTimes(4);
  });

  test("login unwraps DEK and returns unlock token", async () => {
    const hashedPassword = await Bun.password.hash("pw123");

    selectQueue.push([
      {
        id: 9,
        username: "alice",
        name: "Alice",
        password: hashedPassword,
      },
    ]);
    selectQueue.push([
      {
        userId: 9,
        kdfSalt: "salt",
        kdfTimeCost: 3,
        kdfMemoryCostKib: 65536,
        kdfParallelism: 1,
        kdfKeyLength: 32,
        wrapIv: "iv",
        wrapTag: "tag",
        wrappedDek: "wrapped",
      } as UserKeyRow,
    ]);

    readMemoryMock.mockResolvedValue({ content: "exists" } as any);

    const response = await login(
      createContext({ username: "alice", password: "pw123" }),
    );

    expect(response.status).toBe(200);
    const payload = (await response.json()) as { unlockToken: string; id: number };

    expect(payload.id).toBe(9);
    expect(payload.unlockToken).toBe("unlock-token");
    expect(unwrapDekMock).toHaveBeenCalledTimes(1);
    expect(createUnlockSessionMock).toHaveBeenCalledWith(9, expect.any(Buffer));
    expect(ensureDefaultSoulMock).toHaveBeenCalledWith(9, "default");
  });

  test("login rejects wrong password", async () => {
    const hashedPassword = await Bun.password.hash("right-password");
    selectQueue.push([
      {
        id: 5,
        username: "alice",
        name: "Alice",
        password: hashedPassword,
      } as UserRow,
    ]);

    const response = await login(
      createContext({ username: "alice", password: "wrong-password" }),
    );

    expect(response.status).toBe(401);
    const payload = (await response.json()) as { error: string };
    expect(payload.error).toBe("Invalid credentials");
    expect(unwrapDekMock).toHaveBeenCalledTimes(0);
  });
});
