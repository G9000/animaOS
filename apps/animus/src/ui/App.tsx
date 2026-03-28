import React, { useState, useCallback, useEffect } from "react";
import { Box, useApp, useInput } from "ink";
import { Header } from "./Header";
import { Chat, type ChatEntry } from "./Chat";
import { Input } from "./Input";
import { Spinner } from "./Spinner";
import { Approval } from "./Approval";
import { ConnectionManager, type ConnectionStatus } from "../client/connection";
import { executeTool } from "../tools/executor";
import { addSessionRule, type PermissionDecision } from "../tools/permissions";
import { ACTION_TOOL_SCHEMAS } from "../tools/registry";
import type { AnimusConfig } from "../client/auth";
import type { ServerMessage, ToolExecuteMessage } from "../client/protocol";
import { setAskUserCallback, clearAskUserCallback } from "../tools/ask_user";
import { hooks } from "../hooks";

interface AppProps {
  config: AnimusConfig;
}

const SLASH_COMMANDS: Record<string, string> = {
  "/quit": "Exit animus",
  "/exit": "Exit animus",
  "/clear": "Clear chat history",
  "/plan": "Toggle plan mode",
  "/cancel": "Cancel current operation",
  "/reconnect": "Force reconnect to server",
  "/help": "Show available commands",
};

export function App({ config }: AppProps) {
  const { exit } = useApp();
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [pendingApproval, setPendingApproval] = useState<ToolExecuteMessage | null>(null);
  const [approvalResolver, setApprovalResolver] = useState<((d: PermissionDecision) => void) | null>(null);
  const [connection, setConnection] = useState<ConnectionManager | null>(null);
  const [model, setModel] = useState<string | undefined>();
  const [mode, setMode] = useState<"normal" | "plan">("normal");
  // For ask_user: question displayed + resolver to return the answer
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [questionResolver, setQuestionResolver] = useState<((answer: string | null) => void) | null>(null);

  const addEntry = useCallback((entry: ChatEntry) => {
    setEntries((prev) => [...prev, entry]);
  }, []);

  useEffect(() => {
    // Wire ask_user to TUI
    setAskUserCallback(async (question: string) => {
      return new Promise<string | null>((resolve) => {
        setPendingQuestion(question);
        setQuestionResolver(() => resolve);
      });
    });

    const conn = new ConnectionManager(config, ACTION_TOOL_SCHEMAS, {
      onStatusChange: setStatus,
      onError: (err) => addEntry({ type: "error", content: err.message }),
      onReconnect: () => {
        // Reset UI state on reconnect — server won't resend turn_complete
        setIsThinking(false);
        addEntry({ type: "assistant", content: "[Reconnected to server]" });
      },
      onMessage: async (msg: ServerMessage) => {
        // Emit message:received hook
        hooks.emit("message:received", { ...msg }).catch(() => {});

        switch (msg.type) {
          case "stream_token":
            setIsThinking(false);
            setEntries((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.type === "assistant" && last.streaming) {
                const updated = [...prev];
                updated[updated.length - 1] = {
                  ...last,
                  content: last.content + msg.token,
                };
                return updated;
              }
              return [...prev, { type: "assistant", content: msg.token, streaming: true }];
            });
            break;
          case "assistant_message":
            if (!msg.partial) {
              addEntry({ type: "assistant", content: msg.content });
              setIsThinking(false);
            }
            break;
          case "reasoning":
            setIsThinking(false);
            setEntries((prev) => {
              const last = prev[prev.length - 1];
              if (last && last.type === "reasoning" && last.streaming) {
                const updated = [...prev];
                updated[updated.length - 1] = {
                  ...last,
                  content: last.content + msg.content,
                };
                return updated;
              }
              return [...prev, { type: "reasoning", content: msg.content, streaming: true }];
            });
            break;
          case "tool_call":
            // Server-side tool call — show for observability
            addEntry({
              type: "tool_call",
              content: "",
              toolCallId: msg.tool_call_id,
              toolName: msg.tool_name,
              toolArgs: msg.args,
              toolStatus: "running",
            });
            break;
          case "tool_return":
            // Match by tool_call_id for correctness when same tool runs concurrently
            setEntries((prev) => {
              const updated = [...prev];
              for (let j = updated.length - 1; j >= 0; j--) {
                if (
                  updated[j].type === "tool_call" &&
                  updated[j].toolCallId === msg.tool_call_id
                ) {
                  updated[j] = { ...updated[j], content: msg.result, toolStatus: "success" };
                  break;
                }
              }
              return updated;
            });
            break;
          case "tool_execute":
            addEntry({
              type: "tool_call",
              content: "",
              toolCallId: msg.tool_call_id,
              toolName: msg.tool_name,
              toolArgs: msg.args,
              toolStatus: "running",
            });
            {
              const result = await executeTool(msg, async (toolName, args) => {
                return new Promise<PermissionDecision>((resolve) => {
                  setPendingApproval(msg);
                  setApprovalResolver(() => resolve);
                });
              });
              setEntries((prev) => {
                const updated = [...prev];
                let idx = -1;
                for (let j = updated.length - 1; j >= 0; j--) {
                  if (updated[j].type === "tool_call" && updated[j].toolCallId === msg.tool_call_id) {
                    idx = j;
                    break;
                  }
                }
                if (idx >= 0) {
                  updated[idx] = { ...updated[idx], content: result.result, toolStatus: result.status };
                }
                return updated;
              });
              conn.send({ type: "tool_result", ...result });
            }
            break;
          case "approval_required":
            // Server-initiated approval — show approval UI
            setPendingApproval({
              type: "tool_execute",
              tool_call_id: msg.tool_call_id,
              tool_name: msg.tool_name,
              args: msg.args,
            });
            setApprovalResolver(() => (decision: PermissionDecision) => {
              conn.send({
                type: "approval_response",
                run_id: msg.run_id,
                tool_call_id: msg.tool_call_id,
                approved: decision !== "deny",
                reason: decision === "deny" ? "User denied" : undefined,
              });
            });
            break;
          case "turn_complete":
            setIsThinking(false);
            setModel(msg.model);
            // Finalize any streaming entries (assistant or reasoning)
            setEntries((prev) => {
              let changed = false;
              const updated = prev.map((e) => {
                if (e.streaming) {
                  changed = true;
                  return { ...e, streaming: false };
                }
                return e;
              });
              return changed ? updated : prev;
            });
            break;
          case "error":
            addEntry({ type: "error", content: msg.message });
            setIsThinking(false);
            break;
        }
      },
    });

    // Emit session:start hook
    hooks.emit("session:start", {
      serverUrl: config.serverUrl,
      username: config.username || "unknown",
    }).catch(() => {});

    conn.connect();
    setConnection(conn);
    return () => {
      hooks.emit("session:end", { reason: "user" }).catch(() => {});
      clearAskUserCallback();
      conn.disconnect();
    };
  }, [config, addEntry]);

  // Ctrl+C to cancel current operation
  useInput((_input, key) => {
    if (key.ctrl && _input === "c") {
      if (isThinking) {
        connection?.send({ type: "cancel" });
        setIsThinking(false);
        addEntry({ type: "assistant", content: "[Cancelled]" });
      }
    }
  });

  const handleSubmit = useCallback((text: string) => {
    // If there's a pending ask_user question, resolve it
    if (pendingQuestion && questionResolver) {
      questionResolver(text);
      setPendingQuestion(null);
      setQuestionResolver(null);
      return;
    }

    if (text === "/quit" || text === "/exit") {
      exit();
      return;
    }
    if (text === "/clear") {
      setEntries([]);
      return;
    }
    if (text === "/plan") {
      const next = mode === "plan" ? "normal" : "plan";
      setMode(next);
      connection?.send({ type: "set_mode", mode: next });
      addEntry({ type: "assistant", content: `Mode: ${next}` });
      return;
    }
    if (text === "/cancel") {
      if (isThinking) {
        connection?.send({ type: "cancel" });
        setIsThinking(false);
        addEntry({ type: "assistant", content: "[Cancelled]" });
      } else {
        addEntry({ type: "assistant", content: "Nothing to cancel" });
      }
      return;
    }
    if (text === "/reconnect") {
      connection?.disconnect();
      connection?.connect();
      addEntry({ type: "assistant", content: "Reconnecting..." });
      return;
    }
    if (text === "/help") {
      const helpText = Object.entries(SLASH_COMMANDS)
        .map(([cmd, desc]) => `  ${cmd.padEnd(14)} ${desc}`)
        .join("\n");
      addEntry({ type: "assistant", content: `Available commands:\n${helpText}` });
      return;
    }
    // Unknown slash command
    if (text.startsWith("/")) {
      addEntry({ type: "error", content: `Unknown command: ${text}. Type /help for available commands.` });
      return;
    }

    addEntry({ type: "user", content: text });
    setIsThinking(true);
    connection?.send({ type: "user_message", message: text });
  }, [connection, addEntry, exit, mode, isThinking, pendingQuestion, questionResolver]);

  const handleApproval = useCallback((decision: "allow" | "deny" | "always") => {
    if (decision === "always" && pendingApproval) {
      addSessionRule(pendingApproval.tool_name);
    }
    approvalResolver?.(decision === "deny" ? "deny" : "allow");
    setPendingApproval(null);
    setApprovalResolver(null);
  }, [pendingApproval, approvalResolver]);

  return (
    <Box flexDirection="column" height="100%">
      <Header connectionStatus={status} model={model} cwd={process.cwd()} mode={mode} />
      <Chat entries={entries} />
      {isThinking && <Spinner />}
      {pendingApproval && (
        <Approval
          toolName={pendingApproval.tool_name}
          args={pendingApproval.args}
          onDecision={handleApproval}
        />
      )}
      {pendingQuestion && (
        <Box marginLeft={2}>
          <Box>{`[?] ${pendingQuestion}`}</Box>
        </Box>
      )}
      <Input
        onSubmit={handleSubmit}
        disabled={isThinking || (!!pendingApproval && !pendingQuestion)}
        placeholder={pendingQuestion ? "Type your answer..." : undefined}
      />
    </Box>
  );
}
