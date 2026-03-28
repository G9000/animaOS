// apps/animus/src/tools/executor.ts
import type { ToolExecuteMessage } from "../client/protocol";
import { executeBash } from "./bash";
import { executeRead } from "./read";
import { executeWrite } from "./write";
import { executeEdit } from "./edit";
import { executeGrep } from "./grep";
import { executeGlob } from "./glob";
import { executeListDir } from "./list_dir";
import { executeMultiEdit } from "./multi_edit";
import { executeAskUser } from "./ask_user";
import { checkPermission, type PermissionDecision } from "./permissions";
import { hooks } from "../hooks";

export interface ExecutionResult {
  tool_call_id: string;
  status: "success" | "error";
  result: string;
  stdout?: string[];
  stderr?: string[];
}

export type ApprovalCallback = (
  toolName: string,
  args: Record<string, unknown>,
) => Promise<PermissionDecision>;

export async function executeTool(
  msg: ToolExecuteMessage,
  onApproval?: ApprovalCallback,
): Promise<ExecutionResult> {
  const { tool_call_id, tool_name, args } = msg;

  const decision = checkPermission(tool_name, args);
  if (decision === "ask" && onApproval) {
    const userDecision = await onApproval(tool_name, args);
    if (userDecision === "deny") {
      return {
        tool_call_id,
        status: "error",
        result: "User denied tool execution",
      };
    }
  } else if (decision === "deny") {
    return {
      tool_call_id,
      status: "error",
      result: "Tool execution denied by permission policy",
    };
  }

  // Emit tool:before hook
  await hooks.emit("tool:before", { toolName: tool_name, args, toolCallId: tool_call_id });
  const startTime = Date.now();

  try {
    let result: {
      status: "success" | "error";
      result: string;
      stdout?: string[];
      stderr?: string[];
    };

    // Args come as Record<string, unknown> from the wire protocol;
    // cast through unknown to satisfy strict TS.
    const a = args as unknown;

    switch (tool_name) {
      case "bash":
        result = await executeBash(a as Parameters<typeof executeBash>[0]);
        break;
      case "read_file":
        result = executeRead(a as Parameters<typeof executeRead>[0]);
        break;
      case "write_file":
        result = executeWrite(a as Parameters<typeof executeWrite>[0]);
        break;
      case "edit_file":
        result = executeEdit(a as Parameters<typeof executeEdit>[0]);
        break;
      case "grep":
        result = executeGrep(a as Parameters<typeof executeGrep>[0]);
        break;
      case "glob":
        result = executeGlob(a as Parameters<typeof executeGlob>[0]);
        break;
      case "list_dir":
        result = executeListDir(a as Parameters<typeof executeListDir>[0]);
        break;
      case "multi_edit":
        result = executeMultiEdit(
          a as Parameters<typeof executeMultiEdit>[0],
        );
        break;
      case "ask_user":
        result = await executeAskUser(
          a as Parameters<typeof executeAskUser>[0],
        );
        break;
      default:
        result = { status: "error", result: `Unknown tool: ${tool_name}` };
    }

    // Emit tool:after hook
    await hooks.emit("tool:after", {
      toolName: tool_name,
      args,
      toolCallId: tool_call_id,
      status: result.status,
      durationMs: Date.now() - startTime,
    });

    return { tool_call_id, ...result };
  } catch (err) {
    // Emit tool:after hook for errors too
    await hooks.emit("tool:after", {
      toolName: tool_name,
      args,
      toolCallId: tool_call_id,
      status: "error",
      durationMs: Date.now() - startTime,
    });

    return {
      tool_call_id,
      status: "error",
      result: err instanceof Error ? err.message : String(err),
    };
  }
}
