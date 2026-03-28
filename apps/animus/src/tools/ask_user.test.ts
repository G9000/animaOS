import { describe, test, expect, afterEach } from "bun:test";
import { executeAskUser, setAskUserCallback, clearAskUserCallback } from "./ask_user";

describe("ask_user tool", () => {
  afterEach(() => {
    clearAskUserCallback();
  });

  test("returns error when no callback registered", async () => {
    const result = await executeAskUser({ question: "What is your name?" });
    expect(result.status).toBe("error");
    expect(result.result).toContain("no callback");
  });

  test("returns user answer when callback is set", async () => {
    setAskUserCallback(async (question) => {
      expect(question).toBe("Pick a color");
      return "blue";
    });

    const result = await executeAskUser({ question: "Pick a color" });
    expect(result.status).toBe("success");
    expect(result.result).toBe("blue");
  });

  test("returns error when user declines (null)", async () => {
    setAskUserCallback(async () => null);

    const result = await executeAskUser({ question: "Anything?" });
    expect(result.status).toBe("error");
    expect(result.result).toContain("declined");
  });

  test("catches callback errors", async () => {
    setAskUserCallback(async () => {
      throw new Error("input broken");
    });

    const result = await executeAskUser({ question: "test" });
    expect(result.status).toBe("error");
    expect(result.result).toContain("input broken");
  });
});
