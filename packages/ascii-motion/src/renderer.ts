import { sobelEdge } from "./sobel.ts";
import { GSETS } from "./glyphs.ts";

export interface FrameData {
  gray: Uint8Array;
  rgb: Uint8Array;
  ts: number[];
  w: number;
  h: number;
  duration: number;
  count: number;
  srcW: number;
  srcH: number;
}

export interface RenderOptions {
  glyphSet: string;
  contrast: number;
  brightness: number;
  invert: boolean;
  color: boolean;
  edgeDetect: boolean;
}

const MONO = '"SFMono-Regular",ui-monospace,Menlo,Consolas,monospace';
// Monospace char width/height ratio — must match the 0.48 factor used in fetchAndDecode
const CHAR_ASPECT = 0.48;

export function paintFrame(
  canvas: HTMLCanvasElement,
  data: FrameData,
  idx: number,
  opts: RenderOptions,
): void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;

  const { w, h } = data;
  const ramp = GSETS[opts.glyphSet] ?? GSETS["K·▪"];
  const n = ramp.length - 1;

  // Cover scaling: pick the larger scale so the grid always fills the canvas
  // without distorting character proportions (cw/ch must equal CHAR_ASPECT).
  const scaleW = canvas.width / w;
  const scaleH = canvas.height / (h / CHAR_ASPECT);
  const scale  = Math.max(scaleW, scaleH);
  const cw = scale;
  const ch = scale / CHAR_ASPECT;

  // Center the grid; cover may crop edges
  const ox = (canvas.width  - cw * w) / 2;
  const oy = (canvas.height - ch * h) / 2;

  const fs = Math.max(2, ch * 0.92);

  ctx.fillStyle = opts.invert ? "#f0f0f0" : "#000";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.font = `bold ${fs}px ${MONO}`;
  ctx.textBaseline = "top";
  ctx.textAlign = "center";

  const off = idx * w * h;
  let gray: Uint8Array = new Uint8Array(data.gray.buffer, off, w * h);
  if (opts.edgeDetect) gray = sobelEdge(gray, w, h);

  const halfCw = cw / 2;

  for (let y = 0; y < h; y++) {
    const py = oy + y * ch;
    if (py + ch < 0 || py > canvas.height) continue;
    for (let x = 0; x < w; x++) {
      const px = ox + x * cw;
      if (px + cw < 0 || px > canvas.width) continue;

      const i = y * w + x;
      let lum = gray[i] + opts.brightness;
      lum = ((lum / 255 - 0.5) * opts.contrast + 0.5) * 255;
      lum = Math.max(0, Math.min(255, lum));
      let t = lum / 255;
      if (opts.invert) t = 1 - t;
      const glyph = ramp[Math.round(t * n)];
      if (glyph === " ") continue;

      if (opts.color && data.rgb) {
        const ci = (off + i) * 3;
        const r = data.rgb[ci], g = data.rgb[ci + 1], b = data.rgb[ci + 2];
        if (Math.max(r, g, b) > 20) {
          const avg = (r + g + b) / 3;
          const f = 1.4;
          ctx.fillStyle = `rgb(${Math.min(255, avg + (r - avg) * f) | 0},${Math.min(255, avg + (g - avg) * f) | 0},${Math.min(255, avg + (b - avg) * f) | 0})`;
        } else {
          ctx.fillStyle = opts.invert ? "#222" : "#ddd";
        }
      } else {
        ctx.fillStyle = opts.invert ? "#111" : "#e4e4e4";
      }

      ctx.fillText(glyph, px + halfCw, py);
    }
  }
}

export function fitCanvas(
  canvas: HTMLCanvasElement,
  container: HTMLElement,
  data: FrameData,
): void {
  const charW = 8, charH = 14;
  const maxW = container.clientWidth - 24;
  const maxH = container.clientHeight - 24;
  const scaleW = maxW / (data.w * charW);
  const scaleH = maxH / (data.h * charH);
  const scale = Math.max(0.3, Math.min(scaleW, scaleH));
  canvas.width = Math.floor(data.w * charW * scale);
  canvas.height = Math.floor(data.h * charH * scale);
}
