// apps/animus/src/tools/bash.ts

import { truncateOutput, LIMITS } from "./truncation";

export interface BashArgs {
  command: string;
  timeout?: number;
  cwd?: string;
}

interface ToolResult {
  status: "success" | "error";
  result: string;
  stdout?: string[];
  stderr?: string[];
}

export async function executeBash(args: BashArgs): Promise<ToolResult> {
  const { command, timeout = 120000, cwd = process.cwd() } = args;

  try {
    const proc = Bun.spawn(["bash", "-c", command], {
      cwd,
      env: { ...process.env },
      stdout: "pipe",
      stderr: "pipe",
    });

    // Race the process against a timeout
    let timer: ReturnType<typeof setTimeout>;
    const timeoutPromise = new Promise<"timeout">((resolve) => {
      timer = setTimeout(() => resolve("timeout"), timeout);
    });

    const raceResult = await Promise.race([
      proc.exited.then(() => "done" as const),
      timeoutPromise,
    ]);
    clearTimeout(timer!);

    if (raceResult === "timeout") {
      proc.kill();
      return {
        status: "error",
        result: `Command timed out after ${timeout}ms`,
        stdout: [],
        stderr: [],
      };
    }

    const stdoutText = proc.stdout ? await new Response(proc.stdout).text() : "";
    const stderrText = proc.stderr ? await new Response(proc.stderr).text() : "";
    const exitCode = proc.exitCode;

    const stdoutArr = stdoutText ? [stdoutText] : [];
    const stderrArr = stderrText ? [stderrText] : [];

    // Smart truncation — preserves error-relevant lines in the middle
    const { content: output } = truncateOutput(stdoutText || stderrText, {
      maxChars: LIMITS.bash.chars,
      maxLines: LIMITS.bash.lines,
      toolName: "bash",
    });

    return {
      status: exitCode === 0 ? "success" : "error",
      result: output,
      stdout: stdoutArr,
      stderr: stderrArr,
    };
  } catch (err) {
    return {
      status: "error",
      result: err instanceof Error ? err.message : String(err),
      stdout: [],
      stderr: [],
    };
  }
}
