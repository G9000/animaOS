import React from "react";
import { Box, Text } from "ink";

interface ToolCallProps {
  toolName: string;
  args: Record<string, unknown>;
  result?: string;
  status?: "running" | "success" | "error";
}

export function ToolCall({ toolName, args, result, status = "running" }: ToolCallProps) {
  const statusColor = status === "success" ? "green" : status === "error" ? "red" : "yellow";
  const argsPreview = Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === "string" ? v.slice(0, 80) : JSON.stringify(v)}`)
    .join(", ");

  return (
    <Box flexDirection="column" marginY={0}>
      <Text>
        <Text color={statusColor}>{">"}</Text>
        <Text bold> {toolName}</Text>
        <Text dimColor>({argsPreview})</Text>
      </Text>
      {result && (
        <Box marginLeft={2}>
          <Text dimColor>{result.slice(0, 500)}{result.length > 500 ? "..." : ""}</Text>
        </Box>
      )}
    </Box>
  );
}
