import React, { useState, useCallback, useEffect } from "react";
import { Box, useApp } from "ink";
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

interface AppProps {
  config: AnimusConfig;
}

export function App({ config }: AppProps) {
  const { exit } = useApp();
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [isThinking, setIsThinking] = useState(false);
  const [pendingApproval, setPendingApproval] = useState<ToolExecuteMessage | null>(null);
  const [approvalResolver, setApprovalResolver] = useState<((d: PermissionDecision) => void) | null>(null);
  const [connection, setConnection] = useState<ConnectionManager | null>(null);
  const [model, setModel] = useState<string | undefined>();

  const addEntry = useCallback((entry: ChatEntry) => {
    setEntries((prev) => [...prev, entry]);
  }, []);

  useEffect(() => {
    const conn = new ConnectionManager(config, ACTION_TOOL_SCHEMAS, {
      onStatusChange: setStatus,
      onError: (err) => addEntry({ type: "error", content: err.message }),
      onMessage: async (msg: ServerMessage) => {
        switch (msg.type) {
          case "assistant_message":
            if (!msg.partial) {
              addEntry({ type: "assistant", content: msg.content });
              setIsThinking(false);
            }
            break;
          case "tool_execute":
            addEntry({
              type: "tool_call",
              content: "",
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
                // Find the last running tool_call entry for this tool
                let idx = -1;
                for (let j = updated.length - 1; j >= 0; j--) {
                  if (updated[j].type === "tool_call" && updated[j].toolName === msg.tool_name && updated[j].toolStatus === "running") {
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
          case "turn_complete":
            setIsThinking(false);
            setModel(msg.model);
            break;
          case "error":
            addEntry({ type: "error", content: msg.message });
            setIsThinking(false);
            break;
        }
      },
    });
    conn.connect();
    setConnection(conn);
    return () => conn.disconnect();
  }, [config, addEntry]);

  const handleSubmit = useCallback((text: string) => {
    if (text === "/quit" || text === "/exit") {
      exit();
      return;
    }
    if (text === "/clear") {
      setEntries([]);
      return;
    }
    addEntry({ type: "user", content: text });
    setIsThinking(true);
    connection?.send({ type: "user_message", message: text });
  }, [connection, addEntry, exit]);

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
      <Header connectionStatus={status} model={model} cwd={process.cwd()} />
      <Chat entries={entries} />
      {isThinking && <Spinner />}
      {pendingApproval && (
        <Approval
          toolName={pendingApproval.tool_name}
          args={pendingApproval.args}
          onDecision={handleApproval}
        />
      )}
      <Input onSubmit={handleSubmit} disabled={isThinking || !!pendingApproval} />
    </Box>
  );
}
