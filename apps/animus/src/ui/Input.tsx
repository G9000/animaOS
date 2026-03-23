import React, { useState } from "react";
import { Box, Text } from "ink";
import TextInput from "ink-text-input";

interface InputProps {
  onSubmit: (value: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

export function Input({ onSubmit, disabled = false, placeholder = "Type a message..." }: InputProps) {
  const [value, setValue] = useState("");

  const handleSubmit = (text: string) => {
    if (text.trim() && !disabled) {
      onSubmit(text.trim());
      setValue("");
    }
  };

  return (
    <Box>
      <Text bold color="blue">{"> "}</Text>
      {disabled ? (
        <Text dimColor>{placeholder}</Text>
      ) : (
        <TextInput
          value={value}
          onChange={setValue}
          onSubmit={handleSubmit}
          placeholder={placeholder}
        />
      )}
    </Box>
  );
}
