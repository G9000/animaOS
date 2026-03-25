// Old spinning version: smaller 30x15 grid with 2D rotation + bilinear sampling + sparkle halo
import { useState, useEffect, useRef } from "react";
import { LOGO_SVG_PATH, DENSITY, hash } from "./constants";

const SPARKLES = [".", ":", "*", "+", "°", "·"];

export function useAnimaSymbolSpinning() {
  const [frame, setFrame] = useState("");
  const alphaRef = useRef<number[][]>([]);
  const readyRef = useRef(false);
  const tRef = useRef(0);

  useEffect(() => {
    const rasterW = 160;
    const rasterH = 164;
    const canvas = document.createElement("canvas");
    canvas.width = rasterW;
    canvas.height = rasterH;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const path = new Path2D(LOGO_SVG_PATH);
    ctx.scale(rasterW / 36, rasterH / 37);
    ctx.fillStyle = "white";
    ctx.fill(path);

    const imageData = ctx.getImageData(0, 0, rasterW, rasterH);

    const cols = 30;
    const rows = 15;
    const cellW = rasterW / cols;
    const cellH = rasterH / rows;
    const alpha: number[][] = [];

    for (let r = 0; r < rows; r++) {
      alpha[r] = [];
      for (let c = 0; c < cols; c++) {
        let sum = 0;
        let count = 0;
        const y0 = Math.floor(r * cellH);
        const y1 = Math.min(rasterH, Math.floor((r + 1) * cellH));
        const x0 = Math.floor(c * cellW);
        const x1 = Math.min(rasterW, Math.floor((c + 1) * cellW));
        for (let py = y0; py < y1; py++) {
          for (let px = x0; px < x1; px++) {
            sum += imageData.data[(py * rasterW + px) * 4 + 3];
            count++;
          }
        }
        alpha[r][c] = count > 0 ? sum / count / 255 : 0;
      }
    }

    alphaRef.current = alpha;
    readyRef.current = true;
  }, []);

  useEffect(() => {
    const render = () => {
      if (!readyRef.current) return;
      const alpha = alphaRef.current;
      const rows = alpha.length;
      const cols = alpha[0].length;
      const t = tRef.current;

      const padX = 6;
      const padY = 2;
      const totalCols = cols + padX * 2;
      const totalRows = rows + padY * 2;
      const cx = totalCols / 2;
      const cy = totalRows / 2;

      const angle = t * 0.02;
      const cosA = Math.cos(angle);
      const sinA = Math.sin(angle);

      const sampleAlpha = (c: number, r: number): number => {
        if (c < 0 || c >= cols - 1 || r < 0 || r >= rows - 1) return 0;
        const ci = Math.floor(c);
        const ri = Math.floor(r);
        const cf = c - ci;
        const rf = r - ri;
        return (
          alpha[ri][ci] * (1 - cf) * (1 - rf) +
          alpha[ri][ci + 1] * cf * (1 - rf) +
          alpha[ri + 1][ci] * (1 - cf) * rf +
          alpha[ri + 1][ci + 1] * cf * rf
        );
      };

      let result = "";

      for (let fy = 0; fy < totalRows; fy++) {
        for (let fx = 0; fx < totalCols; fx++) {
          const dx = fx - cx;
          const dy = (fy - cy) * 2;
          const rx = dx * cosA - dy * sinA;
          const ry = (dx * sinA + dy * cosA) / 2;
          const ar = ry + rows / 2;
          const ac = rx + cols / 2;

          const a = sampleAlpha(ac, ar);

          if (a > 0.08) {
            const w1 = Math.sin(fx * 0.2 - t * 0.08 + fy * 0.15) * 0.5 + 0.5;
            const w2 = Math.sin(fx * 0.1 + t * 0.12 - fy * 0.3) * 0.5 + 0.5;
            const w3 = Math.cos((fx + fy) * 0.13 + t * 0.04) * 0.5 + 0.5;
            const pulse = Math.sin(t * 0.025) * 0.12 + 0.88;
            const flick = hash(fx, fy, t % 47) > 0.93 ? 0.2 : 0;
            const combined = w1 * 0.4 + w2 * 0.35 + w3 * 0.25;
            const brightness = Math.min(
              1,
              a * (0.5 + 0.5 * combined) * pulse + flick,
            );
            const idx = Math.floor(brightness * (DENSITY.length - 1));
            result += DENSITY[Math.max(0, Math.min(DENSITY.length - 1, idx))];
          } else {
            const sdx = (fx - cx) / cx;
            const sdy = (fy - cy) / cy;
            const dist = Math.sqrt(sdx * sdx + sdy * sdy);
            const sAngle = Math.atan2(sdy, sdx);

            const petals = Math.cos((sAngle - t * 0.03) * 6) * 0.5 + 0.5;
            const bloom = Math.sin(t * 0.04) * 0.15 + 0.7;
            const ring = 1 - Math.abs(dist - bloom) * 3;
            const sparkleIntensity = Math.max(0, ring) * petals;

            const rng = hash(fx, fy, Math.floor(t * 0.25));

            if (sparkleIntensity > 0.5 && rng > 0.6) {
              const si = Math.floor(hash(fx, fy, t % 31) * SPARKLES.length);
              result += SPARKLES[si];
            } else if (sparkleIntensity > 0.3 && rng > 0.8) {
              result += "·";
            } else if (
              dist < 1.1 &&
              hash(fx, fy, Math.floor(t * 0.15)) > 0.96
            ) {
              result += "·";
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
