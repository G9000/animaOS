import React from "react";
import { Box, Text } from "ink";
import { ToolCall } from "./ToolCall";

export interface ChatEntry {
  type: "user" | "assistant" | "tool_call" | "error";
  content: string;
  toolName?: string;
  toolArgs?: Record<string, unknown>;
  toolStatus?: "running" | "success" | "error";
}

interface ChatProps {
  entries: ChatEntry[];
}

export function Chat({ entries }: ChatProps) {
  return (
    <Box flexDirection="column" flexGrow={1}>
      {entries.map((entry, i) => {
        switch (entry.type) {
          case "user":
            return (
              <Box key={i} marginY={0}>
                <Text bold color="blue">You: </Text>
                <Text>{entry.content}</Text>
              </Box>
            );
          case "assistant":
            return (
              <Box key={i} marginY={0}>
                <Text bold color="green">Anima: </Text>
                <Text>{entry.content}</Text>
              </Box>
            );
          case "tool_call":
            return (
              <ToolCall
                key={i}
                toolName={entry.toolName!}
                args={entry.toolArgs!}
                result={entry.content}
                status={entry.toolStatus}
              />
            );
          case "error":
            return (
              <Box key={i}>
                <Text color="red">Error: {entry.content}</Text>
              </Box>
            );
          default:
            return null;
        }
      })}
    </Box>
  );
}
