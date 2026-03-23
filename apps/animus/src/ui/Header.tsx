import React from "react";
import { Box, Text } from "ink";
import type { ConnectionStatus } from "../client/connection";

interface HeaderProps {
  connectionStatus: ConnectionStatus;
  model?: string;
  cwd: string;
}

const STATUS_COLORS: Record<ConnectionStatus, string> = {
  connected: "green",
  connecting: "yellow",
  authenticating: "yellow",
  disconnected: "red",
};

export function Header({ connectionStatus, model, cwd }: HeaderProps) {
  return (
    <Box borderStyle="single" paddingX={1} flexDirection="row" justifyContent="space-between">
      <Text bold>anima</Text>
      <Text>{model ?? "no model"}</Text>
      <Text color={STATUS_COLORS[connectionStatus]}>{connectionStatus}</Text>
      <Text dimColor>{cwd}</Text>
    </Box>
  );
}
