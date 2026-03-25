import { useState, useEffect, useRef } from "react";
import { hash } from "./constants";

const CHARS = " .·:~-=+*";

export interface GlowChar {
  ch: string;
  bright: boolean; // true for the traveling indicator
}

export function useGlowLine(active: boolean, width = 32, speed = 1) {
  const [frame, setFrame] = useState<GlowChar[]>([]);
  const tRef = useRef(0);

  useEffect(() => {
    if (!active) {
      setFrame(Array.from({ length: width }, () => ({ ch: "·", bright: false })));
      return;
    }

    const render = () => {
      const t = tRef.current;
      const result: GlowChar[] = [];
      const cx = width / 2;

      // Pulse ring expanding from center
      const pulseRadius = ((t * 0.12) % (cx + 4));
      const pulseActive = pulseRadius < cx + 2;

      for (let i = 0; i < width; i++) {
        const dx = (i - cx) / cx;
        const dist = Math.abs(dx);
        const distFromCenter = Math.abs(i - cx);

        // Edge fade
        const fade = 1 - dist * dist;

        // Shimmer waves
        const w1 = Math.sin(i * 0.3 - t * 0.1) * 0.5 + 0.5;
        const w2 = Math.cos(i * 0.15 + t * 0.07) * 0.5 + 0.5;
        const pulse = Math.sin(t * 0.04) * 0.15 + 0.85;
        const flick = hash(i, 0, t % 37) > 0.9 ? 0.2 : 0;

        // Pulse ring glow — bright at the expanding ring edge
        const ringDist = Math.abs(distFromCenter - pulseRadius);
        const ringGlow = pulseActive ? Math.max(0, 1 - ringDist / 2.5) : 0;

        const brightness = Math.max(0, fade * (w1 * 0.5 + w2 * 0.5) * pulse + flick + ringGlow * 0.6);
        const idx = Math.floor(Math.min(1, brightness) * (CHARS.length - 1));
        const bright = pulseActive && ringDist < 1.5;

        result.push({
          ch: CHARS[Math.max(0, Math.min(CHARS.length - 1, idx))],
          bright,
        });
      }

      setFrame(result);
      tRef.current += speed;
    };

    const interval = setInterval(render, 55);
    return () => clearInterval(interval);
  }, [active, width, speed]);

  return frame;
}
