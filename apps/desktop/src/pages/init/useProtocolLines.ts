import { useState, useCallback, useEffect, useMemo, useRef } from "react";
import type { Line } from "./constants";

export type AddLineFn = (type: Line["type"], text: string) => void;

export function useProtocolLines() {
  const [lines, setLines] = useState<Line[]>([]);
  const idRef = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  const isRevealing = lines.some((l) => l.revealed.length < l.text.length);

  const addLine = useCallback((type: Line["type"], text: string) => {
    setLines((p) => [
      ...p,
      { id: idRef.current++, type, text, revealed: type === "input" ? text : "" },
    ]);
  }, []);

  // Typewriter character reveal
  useEffect(() => {
    if (!isRevealing) return;
    const t = setTimeout(() => {
      setLines((prev) =>
        prev.map((l) =>
          l.revealed.length < l.text.length
            ? { ...l, revealed: l.text.slice(0, l.revealed.length + 1) }
            : l,
        ),
      );
    }, 18);
    return () => clearTimeout(t);
  }, [lines, isRevealing]);

  // Scroll to bottom on new lines
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);

  const lastQuestion = useMemo(() => {
    for (let i = lines.length - 1; i >= 0; i--) {
      if (lines[i].type === "output") return lines[i];
    }
    return null;
  }, [lines]);

  const lastError = useMemo(() => {
    const questionIdx = lastQuestion ? lines.findIndex((l) => l.id === lastQuestion.id) : -1;
    for (let i = lines.length - 1; i > questionIdx; i--) {
      if (lines[i].type === "error") return lines[i];
    }
    return null;
  }, [lines, lastQuestion]);

  const trimLines = (toLength: number) => setLines((prev) => prev.slice(0, toLength));

  return { lines, addLine, trimLines, isRevealing, lastQuestion, lastError, bottomRef };
}
