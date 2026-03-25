import { useState, useEffect, useRef } from "react";
import { hash } from "./constants";

const ACTIVE_CHARS = ["#", "*", "@", "%", "+"];
const DONE_CHARS = ["·", ":", "~", "-"];
const FUTURE_CHARS = [".", "·", " "];

/**
 * Renders step indicator dots as ASCII characters with subtle glitches.
 * Returns an array of { char, active, done } for each step.
 */
export function useAsciiDots(total: number, current: number) {
  const [frame, setFrame] = useState<string[]>([]);
  const tRef = useRef(0);

  useEffect(() => {
    const render = () => {
      const t = tRef.current;
      const result: string[] = [];

      for (let i = 0; i < total; i++) {
        const isActive = i === current;
        const isDone = i < current;
        const glitch = hash(i, 7, Math.floor(t * 0.06));

        if (isActive) {
          if (glitch > 0.985) {
            result.push(ACTIVE_CHARS[Math.floor(hash(i, 2, t) * ACTIVE_CHARS.length)]);
          } else {
            result.push("*");
          }
        } else if (isDone) {
          if (glitch > 0.985) {
            result.push(DONE_CHARS[Math.floor(hash(i, 3, t) * DONE_CHARS.length)]);
          } else {
            result.push("·");
          }
        } else {
          if (glitch > 0.99) {
            result.push(FUTURE_CHARS[Math.floor(hash(i, 4, t) * FUTURE_CHARS.length)]);
          } else {
            result.push(".");
          }
        }
      }

      setFrame(result);
      tRef.current += 1;
    };

    const interval = setInterval(render, 55);
    render();
    return () => clearInterval(interval);
  }, [total, current]);

  return frame;
}
