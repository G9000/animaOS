// apps/animus/src/tools/ask_user.ts

export interface AskUserArgs {
  question: string;
}

/**
 * Callback that presents the question to the user and returns their response.
 * In TUI mode this shows an input prompt; in headless mode it reads from stdin
 * or returns a deny.
 */
export type AskUserCallback = (question: string) => Promise<string | null>;

let _callback: AskUserCallback | null = null;

/** Set the ask_user handler (called once at startup by TUI or headless). */
export function setAskUserCallback(cb: AskUserCallback): void {
  _callback = cb;
}

/** Clear the callback (for testing). */
export function clearAskUserCallback(): void {
  _callback = null;
}

export async function executeAskUser(
  args: AskUserArgs,
): Promise<{ status: "success" | "error"; result: string }> {
  if (!_callback) {
    return { status: "error", result: "ask_user not available (no callback registered)" };
  }
  try {
    const answer = await _callback(args.question);
    if (answer === null) {
      return { status: "error", result: "User declined to answer" };
    }
    return { status: "success", result: answer };
  } catch (err) {
    return {
      status: "error",
      result: err instanceof Error ? err.message : String(err),
    };
  }
}
