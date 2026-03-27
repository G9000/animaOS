import { describe, test, expect } from "bun:test";
import { executeAskUser } from "./ask_user";

describe("ask_user tool", () => {
  test("returns error (stub behavior)", async () => {
    const result = await executeAskUser({ question: "What is your name?" });
    expect(result.status).toBe("error");
    expect(result.result).toContain("not available");
  });
});
