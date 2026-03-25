import { useState, useEffect, useRef } from "react";
import { hash } from "./constants";

const RESOLVE_CHARS = "·:~-=+*%#@";

/**
 * Renders text with ASCII art animation.
 * Characters materialize from noise into the target string,
 * then glitch and shimmer once resolved.
 */
export function useAsciiText(
  text: string,
  active: boolean,
  speed = 1,
) {
  const [frame, setFrame] = useState("");
  const tRef = useRef(0);

  // Reset on new text
  useEffect(() => {
    tRef.current = 0;
  }, [text]);

  useEffect(() => {
    if (!active || !text) {
      setFrame(text);
      return;
    }

    const render = () => {
      const t = tRef.current;
      const chars = text.split("");
      let result = "";

      for (let i = 0; i < chars.length; i++) {
        const ch = chars[i];

        if (ch === " ") {
          result += " ";
          continue;
        }

        // Resolve wave: fast — all chars resolve within ~15 frames
        const resolveAt = i * 0.4 + 3;
        const resolved = t > resolveAt;

        if (!resolved) {
          // Pre-resolve: light noise, quick pass
          const progress = Math.max(0, t - i * 0.4) / 3;
          const rng = hash(i, 0, Math.floor(t * 0.8));

          if (progress < 0.3) {
            result += rng > 0.6 ? "·" : " ";
          } else if (progress > 0.7 && rng > 0.5) {
            result += ch;
          } else {
            const idx = Math.floor(progress * 3);
            result += RESOLVE_CHARS[Math.min(idx, RESOLVE_CHARS.length - 1)];
          }
        } else {
          // Post-resolve: frequent glitches
          const glitch1 = hash(i, 0, Math.floor(t * 0.03));

          if (glitch1 > 0.997) {
            result += "·";
          } else {
            result += ch;
          }
        }
      }

      setFrame(result);
      tRef.current += speed;
    };

    const interval = setInterval(render, 55);
    return () => clearInterval(interval);
  }, [text, active, speed]);

  return frame;
}
