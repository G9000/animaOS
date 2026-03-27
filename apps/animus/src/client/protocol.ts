// apps/animus/src/client/protocol.ts

// ── Server -> Client ──

export interface AuthOkMessage {
  type: "auth_ok";
  user: { id: number; username: string };
}

export interface ToolExecuteMessage {
  type: "tool_execute";
  tool_call_id: string;
  tool_name: string;
  args: Record<string, unknown>;
}

export interface AssistantMessage {
  type: "assistant_message";
  content: string;
  partial: boolean;
}

export interface ReasoningMessage {
  type: "reasoning";
  content: string;
}

export interface ToolCallMessage {
  type: "tool_call";
  tool_call_id: string;
  tool_name: string;
  args: Record<string, unknown>;
}

export interface ToolReturnMessage {
  type: "tool_return";
  tool_call_id: string;
  tool_name: string;
  result: string;
}

export interface ApprovalRequiredMessage {
  type: "approval_required";
  tool_call_id: string;
  tool_name: string;
  args: Record<string, unknown>;
  run_id: number;
}

export interface TurnCompleteMessage {
  type: "turn_complete";
  response: string;
  model: string;
  provider: string;
  tools_used: string[];
}

export interface ErrorMessage {
  type: "error";
  message: string;
  code: string;
}

export interface StreamTokenMessage {
  type: "stream_token";
  token: string;
}

export type ServerMessage =
  | AuthOkMessage
  | ToolExecuteMessage
  | AssistantMessage
  | ReasoningMessage
  | ToolCallMessage
  | ToolReturnMessage
  | ApprovalRequiredMessage
  | TurnCompleteMessage
  | ErrorMessage
  | StreamTokenMessage;

// ── Client -> Server ──

export interface AuthMessage {
  type: "auth";
  unlockToken?: string;
  username?: string;
  password?: string;
}

export interface UserMessage {
  type: "user_message";
  message: string;
}

export interface ToolResultMessage {
  type: "tool_result";
  tool_call_id: string;
  status: "success" | "error";
  result: string;
  stdout?: string[];
  stderr?: string[];
}

export interface ToolSchemasMessage {
  type: "tool_schemas";
  tools: ToolSchema[];
}

export interface ApprovalResponseMessage {
  type: "approval_response";
  run_id: number;
  tool_call_id: string;
  approved: boolean;
  reason?: string;
}

export interface CancelMessage {
  type: "cancel";
  run_id?: number;
}

export interface SetModeMessage {
  type: "set_mode";
  mode: "normal" | "plan";
}

export type ClientMessage =
  | AuthMessage
  | UserMessage
  | ToolResultMessage
  | ToolSchemasMessage
  | ApprovalResponseMessage
  | CancelMessage
  | SetModeMessage;

// ── Tool Schema ──

export interface ToolSchema {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}
