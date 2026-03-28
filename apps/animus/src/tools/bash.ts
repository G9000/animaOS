// apps/animus/src/tools/bash.ts

export interface BashArgs {
  command: string;
  timeout?: number;
  cwd?: string;
}

export interface ToolResult {
  status: "success" | "error";
  result: string;
  stdout?: string[];
  stderr?: string[];
}

const MAX_OUTPUT_LINES = 500;

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

    let output = stdoutText;
    const lines = output.split("\n");
    if (lines.length > MAX_OUTPUT_LINES) {
      output = `[...truncated ${lines.length - MAX_OUTPUT_LINES} lines...]\n${lines.slice(-MAX_OUTPUT_LINES).join("\n")}`;
    }

    return {
      status: exitCode === 0 ? "success" : "error",
      result: output || stderrText,
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
