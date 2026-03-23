import React, { useState } from "react";
import { Box, Text, useInput } from "ink";

interface ApprovalProps {
  toolName: string;
  args: Record<string, unknown>;
  onDecision: (decision: "allow" | "deny" | "always") => void;
}

export function Approval({ toolName, args, onDecision }: ApprovalProps) {
  const [selected, setSelected] = useState(0);
  const options = ["Allow", "Deny", "Always allow"];

  useInput((_input, key) => {
    if (key.upArrow) setSelected((s) => Math.max(0, s - 1));
    if (key.downArrow) setSelected((s) => Math.min(options.length - 1, s + 1));
    if (key.return) {
      const decisions = ["allow", "deny", "always"] as const;
      onDecision(decisions[selected]);
    }
  });

  const preview = toolName === "bash" ? (args.command as string) : JSON.stringify(args).slice(0, 100);

  return (
    <Box flexDirection="column" borderStyle="round" borderColor="yellow" paddingX={1}>
      <Text bold color="yellow">Permission required</Text>
      <Text><Text bold>{toolName}</Text>: {preview}</Text>
      <Box flexDirection="column" marginTop={1}>
        {options.map((opt, i) => (
          <Text key={opt}>
            {i === selected ? <Text color="cyan">{"> "}</Text> : "  "}
            {opt}
          </Text>
        ))}
      </Box>
    </Box>
  );
}
