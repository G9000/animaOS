// Animated ANIMA block-letter text with layered wave shimmer + particle background
import { useState, useEffect, useRef } from "react";
import { ANIMA_ART, BG_DUST, GLOW, hash } from "./constants";

const ART_W = ANIMA_ART.reduce((m, r) => Math.max(m, r.length), 0);
const ART_H = ANIMA_ART.length;
const FIELD_W = 72;
const FIELD_H = 14;
const PAD_X = Math.floor((FIELD_W - ART_W) / 2);
const PAD_Y = Math.floor((FIELD_H - ART_H) / 2);

export function useAnimaLogo() {
  const [frame, setFrame] = useState("");
  const tRef = useRef(0);

  useEffect(() => {
    const render = () => {
      const t = tRef.current;
      let result = "";

      for (let fy = 0; fy < FIELD_H; fy++) {
        for (let fx = 0; fx < FIELD_W; fx++) {
          const ax = fx - PAD_X;
          const ay = fy - PAD_Y;
          const inArt = ay >= 0 && ay < ART_H && ax >= 0 && ax < ART_W;
          const artCh = inArt ? (ANIMA_ART[ay][ax] ?? " ") : " ";

          if (artCh !== " ") {
            const w1 = Math.sin(fx * 0.12 - t * 0.09 + fy * 0.25);
            const w2 = Math.sin(fx * 0.07 + t * 0.13 - fy * 0.4);
            const w3 = Math.cos((fx + fy) * 0.1 + t * 0.05);
            const pulse = Math.sin(t * 0.04) * 0.12 + 0.88;
            const flicker = hash(fx, fy, t % 60) > 0.92 ? 0.15 : 0;
            const b = ((w1 + w2 + w3) / 3) * 0.5 + 0.5;
            const brightness = Math.min(1, b * pulse + flicker);

            if (brightness > 0.72) result += artCh;
            else if (brightness > 0.52) result += GLOW[3];
            else if (brightness > 0.32) result += GLOW[2];
            else if (brightness > 0.15) result += GLOW[1];
            else result += " ";
          } else {
            const n = hash(fx, fy, Math.floor(t * 0.3));
            const dx = (fx - FIELD_W / 2) / (FIELD_W / 2);
            const dy = (fy - FIELD_H / 2) / (FIELD_H / 2);
            const dist = Math.sqrt(dx * dx + dy * dy);
            const fade = Math.max(0, 1 - dist * 0.9);
            const drift =
              Math.sin(fx * 0.2 + t * 0.04) *
                Math.cos(fy * 0.35 - t * 0.03) *
                0.5 +
              0.5;
            const intensity = n * fade * drift;

            if (intensity > 0.82) {
              result += BG_DUST[7];
            } else if (intensity > 0.7) {
              result += BG_DUST[Math.floor(hash(fx, fy, t % 37) * 3) + 4];
            } else if (intensity > 0.55 && fade > 0.3) {
              result += BG_DUST[4];
            } else {
              result += " ";
            }
          }
        }
        result += "\n";
      }

      setFrame(result);
      tRef.current += 1;
    };

    const interval = setInterval(render, 55);
    return () => clearInterval(interval);
  }, []);

  return frame;
}
