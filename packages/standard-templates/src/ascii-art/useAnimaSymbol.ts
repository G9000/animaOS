// 44x22 grid with layered shimmer, edge glow, sparkle halo + inner ring.
// Optional centerText is rasterised to a per-cell alpha mask (same technique
// as the logo SVG) so the greeting word is formed by brightness-modulated
// DENSITY characters — truly part of the ASCII art, not an overlay.
import { useState, useEffect, useRef } from "react";
import { LOGO_SVG_PATH, DENSITY, SPARKLE_CHARS, hash } from "./constants";

const COLS = 44;
const ROWS = 22;
const PAD_X = 5;
const PAD_Y = 2;
const TOTAL_COLS = COLS + PAD_X * 2; // 54
const TOTAL_ROWS = ROWS + PAD_Y * 2; // 26
const RASTER_SCALE = 4; // canvas supersampling factor

interface SymbolFrame {
  base: string;   // full symbol, text-region cells are spaces
  text: string;   // only text-region chars, everything else spaces
}

export function useAnimaSymbol(speed = 1, centerText?: string): SymbolFrame {
  const [frame, setFrame] = useState<SymbolFrame>({ base: "", text: "" });
  const logoAlphaRef = useRef<number[][]>([]);
  const textAlphaRef = useRef<number[][]>([]);
  const readyRef = useRef(false);
  const tRef = useRef(0);
  const speedRef = useRef(speed);

  useEffect(() => { speedRef.current = speed; }, [speed]);

  // ── Logo SVG raster (runs once) ──────────────────────────────────────
  useEffect(() => {
    const rasterW = 200;
    const rasterH = 206;
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
    const cellW = rasterW / COLS;
    const cellH = rasterH / ROWS;
    const alpha: number[][] = [];

    for (let r = 0; r < ROWS; r++) {
      alpha[r] = [];
      for (let c = 0; c < COLS; c++) {
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

    logoAlphaRef.current = alpha;
    readyRef.current = true;
  }, []);

  // ── Center-text raster (re-runs when word changes) ───────────────────
  useEffect(() => {
    if (!centerText) {
      textAlphaRef.current = [];
      return;
    }

    const cw = TOTAL_COLS * RASTER_SCALE; // 216
    const ch = TOTAL_ROWS * RASTER_SCALE; // 104
    const canvas = document.createElement("canvas");
    canvas.width = cw;
    canvas.height = ch;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    // Auto-size: pick the largest font that fits within 70 % of the canvas
    let fontSize = Math.floor(ch * 0.35);
    ctx.font = `700 ${fontSize}px monospace`;
    const maxW = cw * 0.7;
    const measured = ctx.measureText(centerText);
    if (measured.width > maxW) {
      fontSize = Math.floor(fontSize * maxW / measured.width);
      ctx.font = `700 ${fontSize}px monospace`;
    }

    ctx.fillStyle = "white";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(centerText, cw / 2, ch / 2);

    const imageData = ctx.getImageData(0, 0, cw, ch);
    const cellW = cw / TOTAL_COLS;
    const cellH2 = ch / TOTAL_ROWS;
    const alpha: number[][] = [];

    for (let r = 0; r < TOTAL_ROWS; r++) {
      alpha[r] = [];
      for (let c = 0; c < TOTAL_COLS; c++) {
        let sum = 0;
        let count = 0;
        const y0 = Math.floor(r * cellH2);
        const y1 = Math.min(ch, Math.floor((r + 1) * cellH2));
        const x0 = Math.floor(c * cellW);
        const x1 = Math.min(cw, Math.floor((c + 1) * cellW));
        for (let py = y0; py < y1; py++) {
          for (let px = x0; px < x1; px++) {
            sum += imageData.data[(py * cw + px) * 4 + 3];
            count++;
          }
        }
        alpha[r][c] = count > 0 ? sum / count / 255 : 0;
      }
    }

    textAlphaRef.current = alpha;
  }, [centerText]);

  // ── Render loop ──────────────────────────────────────────────────────
  useEffect(() => {
    const render = () => {
      if (!readyRef.current) return;
      const logoAlpha = logoAlphaRef.current;
      const textAlpha = textAlphaRef.current;
      const hasText = textAlpha.length > 0;
      const t = tRef.current;

      const cx = TOTAL_COLS / 2;
      const cy = TOTAL_ROWS / 2;

      const baseRows: string[] = [];
      const textRows: string[] = [];

      for (let fy = 0; fy < TOTAL_ROWS; fy++) {
        let baseRow = "";
        let textRow = "";
        for (let fx = 0; fx < TOTAL_COLS; fx++) {
          const ar = fy - PAD_Y;
          const ac = fx - PAD_X;
          const inLogo = ar >= 0 && ar < ROWS && ac >= 0 && ac < COLS;
          const a = inLogo ? logoAlpha[ar][ac] : 0;

          // ── Text mask: form letters from density chars ──
          const ta = hasText ? (textAlpha[fy]?.[fx] ?? 0) : 0;

          if (ta > 0.08) {
            // Inside a letter shape — render in text layer, space in base layer
            const pulse = Math.sin(t * 0.035 + fx * 0.12) * 0.1 + 0.9;
            const sparkle = hash(fx, fy, Math.floor(t * 0.18)) > 0.96 ? 0.12 : 0;
            const brightness = Math.min(1, ta * pulse + sparkle);
            const idx = Math.floor(brightness * (DENSITY.length - 1));
            textRow += DENSITY[Math.max(0, Math.min(DENSITY.length - 1, idx))];
            baseRow += " ";
            continue;
          }

          textRow += " ";

          // ── Normal symbol rendering (unchanged) ──
          if (a > 0.08) {
            const w1 = Math.sin(fx * 0.2 - t * 0.08 + fy * 0.15) * 0.5 + 0.5;
            const w2 = Math.sin(fx * 0.09 + t * 0.11 - fy * 0.35) * 0.5 + 0.5;
            const w3 = Math.cos((fx + fy) * 0.12 + t * 0.05) * 0.5 + 0.5;
            const pulse = Math.sin(t * 0.025) * 0.12 + 0.88;
            const flick = hash(fx, fy, t % 47) > 0.92 ? 0.22 : 0;
            const combined = w1 * 0.4 + w2 * 0.35 + w3 * 0.25;
            const brightness = Math.min(
              1,
              a * (0.5 + 0.5 * combined) * pulse + flick,
            );
            const idx = Math.floor(brightness * (DENSITY.length - 1));
            baseRow += DENSITY[Math.max(0, Math.min(DENSITY.length - 1, idx))];
          } else if (inLogo && a > 0.01) {
            const edgePulse =
              Math.sin(fx * 0.3 + fy * 0.2 - t * 0.06) * 0.5 + 0.5;
            baseRow += edgePulse > 0.6 && hash(fx, fy, t % 29) > 0.5 ? "." : " ";
          } else {
            const dx = (fx - cx) / cx;
            const dy = (fy - cy) / cy;
            const dist = Math.sqrt(dx * dx + dy * dy);
            const angle = Math.atan2(dy, dx);

            const petals = Math.cos((angle - t * 0.025) * 6) * 0.5 + 0.5;
            const bloom = Math.sin(t * 0.035) * 0.18 + 0.72;
            const ring = 1 - Math.abs(dist - bloom) * 3.5;
            const intensity = Math.max(0, ring) * petals;

            const innerBloom = Math.sin(t * 0.05 + 1.5) * 0.1 + 0.4;
            const innerRing = 1 - Math.abs(dist - innerBloom) * 5;
            const innerIntensity = Math.max(0, innerRing) * 0.4;

            const totalIntensity = Math.max(intensity, innerIntensity);
            const rng = hash(fx, fy, Math.floor(t * 0.22));

            if (totalIntensity > 0.5 && rng > 0.55) {
              const si = Math.floor(
                hash(fx, fy, t % 31) * SPARKLE_CHARS.length,
              );
              baseRow += SPARKLE_CHARS[si];
            } else if (totalIntensity > 0.25 && rng > 0.75) {
              baseRow += "·";
            } else if (
              dist < 1.15 &&
              hash(fx, fy, Math.floor(t * 0.12)) > 0.95
            ) {
              baseRow += "·";
            } else {
              baseRow += " ";
            }
          }
        }
        baseRows.push(baseRow);
        textRows.push(textRow);
      }

      setFrame({
        base: baseRows.join("\n") + "\n",
        text: textRows.join("\n") + "\n",
      });
      tRef.current += speedRef.current;
    };

    const interval = setInterval(render, 55);
    return () => clearInterval(interval);
  }, []);

  return frame;
}
