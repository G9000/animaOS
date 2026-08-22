import { useEffect, useRef } from "react";
import { demux } from "./demux.ts";
import { paintFrame, type FrameData, type RenderOptions } from "./renderer.ts";

export interface AsciiBackgroundProps {
  src: string;
  /** Pass "image" when src is a blob/object-URL for an image file */
  mediaType?: "image" | "video";
  cols?: number;
  glyphSet?: string;
  contrast?: number;
  brightness?: number;
  invert?: boolean;
  color?: boolean;
  edgeDetect?: boolean;
  className?: string;
  style?: React.CSSProperties;
}

function isImageSrc(src: string, hint?: "image" | "video"): boolean {
  if (hint === "image") return true;
  if (hint === "video") return false;
  return /\.(jpe?g|png|gif|webp|bmp|avif)(\?.*)?$/i.test(src);
}

async function fetchAndDecodeImage(src: string, cols: number): Promise<FrameData> {
  const img = new Image();
  img.crossOrigin = "anonymous";
  img.src = src;
  await new Promise<void>((res, rej) => { img.onload = () => res(); img.onerror = () => rej(new Error("img load")); });
  const { naturalWidth: iw, naturalHeight: ih } = img;
  const rows = Math.max(1, Math.round(cols * (ih / iw) * 0.48));
  const oc = new OffscreenCanvas(cols, rows);
  const ox = oc.getContext("2d")!;
  ox.drawImage(img, 0, 0, cols, rows);
  const id = ox.getImageData(0, 0, cols, rows);
  const gray = new Uint8Array(cols * rows);
  const rgb  = new Uint8Array(cols * rows * 3);
  for (let i = 0; i < gray.length; i++) {
    const p = i * 4;
    gray[i] = Math.round(0.299 * id.data[p] + 0.587 * id.data[p+1] + 0.114 * id.data[p+2]);
    rgb[i*3] = id.data[p]; rgb[i*3+1] = id.data[p+1]; rgb[i*3+2] = id.data[p+2];
  }
  return { gray, rgb, ts: [0], w: cols, h: rows, duration: 86400, count: 1, srcW: iw, srcH: ih };
}

async function fetchAndDecode(src: string, cols: number): Promise<FrameData> {
  const buf = await fetch(src).then(r => r.arrayBuffer());
  const dm = demux(buf);

  const decoded: Array<{ gray: Uint8Array; rgb: Uint8Array; pts: number }> = [];
  let vw = 0, vh = 0;

  await new Promise<void>((res, rej) => {
    const dec = new VideoDecoder({
      output: (frame) => {
        if (!vw) { vw = frame.displayWidth; vh = frame.displayHeight; }
        const rows = Math.round(cols * (vh / vw) * 0.48);
        const oc = new OffscreenCanvas(cols, rows);
        const ox = oc.getContext("2d")!;
        ox.drawImage(frame, 0, 0, cols, rows);
        const id = ox.getImageData(0, 0, cols, rows);
        const gray = new Uint8Array(cols * rows);
        const rgb = new Uint8Array(cols * rows * 3);
        for (let i = 0; i < gray.length; i++) {
          const p = i * 4;
          gray[i] = Math.round(0.299 * id.data[p] + 0.587 * id.data[p+1] + 0.114 * id.data[p+2]);
          rgb[i*3] = id.data[p]; rgb[i*3+1] = id.data[p+1]; rgb[i*3+2] = id.data[p+2];
        }
        decoded.push({ gray, rgb, pts: frame.timestamp });
        frame.close();
      },
      error: rej,
    });

    const cfg: VideoDecoderConfig = { codec: dm.codec, optimizeForLatency: true };
    if (dm.desc) cfg.description = dm.desc as BufferSource;
    dec.configure(cfg);

    for (const s of dm.samples) {
      dec.decode(new EncodedVideoChunk({
        type: s.isKey ? "key" : "delta",
        timestamp: s.timestampUs,
        duration: s.durationUs,
        data: buf.slice(s.offset, s.offset + s.size),
      }));
    }
    dec.flush().then(() => { dec.close(); res(); }).catch(rej);
  });

  if (!decoded.length) throw new Error("No frames decoded");
  decoded.sort((a, b) => a.pts - b.pts);

  const rows = decoded[0].gray.length / cols;
  const allG = new Uint8Array(decoded.length * cols * rows);
  const allR = new Uint8Array(decoded.length * cols * rows * 3);
  const ts: number[] = [];
  const ptsBase = decoded[0].pts;

  for (let i = 0; i < decoded.length; i++) {
    allG.set(decoded[i].gray, i * cols * rows);
    allR.set(decoded[i].rgb, i * cols * rows * 3);
    ts.push((decoded[i].pts - ptsBase) / 1e6);
  }

  return { gray: allG, rgb: allR, ts, w: cols, h: rows, duration: dm.duration, count: decoded.length, srcW: vw, srcH: vh };
}

export function AsciiBackground({
  src,
  mediaType,
  cols = 140,
  glyphSet = "K·▪",
  contrast = 1.2,
  brightness = -5,
  invert = false,
  color = true,
  edgeDetect = false,
  className,
  style,
}: AsciiBackgroundProps) {
  const cvRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number>(0);
  const dataRef = useRef<FrameData | null>(null);
  const tRef = useRef(0);
  const fiRef = useRef(0);
  const optsRef = useRef<RenderOptions>({ glyphSet, contrast, brightness, invert, color, edgeDetect });

  // keep opts in sync without re-running the effect
  optsRef.current = { glyphSet, contrast, brightness, invert, color, edgeDetect };

  useEffect(() => {
    const cv = cvRef.current;
    const wrap = wrapRef.current;
    if (!cv || !wrap) return;

    function syncSize() {
      if (!cv || !wrap) return;
      cv.width = wrap.clientWidth;
      cv.height = wrap.clientHeight;
      const data = dataRef.current;
      if (data) paintFrame(cv, data, fiRef.current, optsRef.current);
    }

    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

    function applyMotionPreference() {
      const data = dataRef.current;
      const canvas = cvRef.current;
      if (!data || !canvas) return;
      if (reducedMotion.matches) {
        if (rafRef.current) cancelAnimationFrame(rafRef.current);
        rafRef.current = 0;
        fiRef.current = 0;
        paintFrame(canvas, data, 0, optsRef.current);
        return;
      }
      if (!rafRef.current) {
        tRef.current = performance.now();
        rafRef.current = requestAnimationFrame(tick);
      }
    }

    function tick(now: number) {
      const data = dataRef.current;
      const canvas = cvRef.current;
      if (!data || !canvas) return;

      const elapsed = (now - tRef.current) / 1000;
      let idx = 0;
      for (let i = 0; i < data.ts.length; i++) {
        if (data.ts[i] <= elapsed) idx = i; else break;
      }
      fiRef.current = idx;
      paintFrame(canvas, data, idx, optsRef.current);

      if (elapsed >= data.duration) { tRef.current = now; fiRef.current = 0; }

      rafRef.current = requestAnimationFrame(tick);
    }

    let cancelled = false;
    const loader = isImageSrc(src, mediaType) ? fetchAndDecodeImage : fetchAndDecode;
    loader(src, cols).then(data => {
      if (cancelled) return;
      dataRef.current = data;
      syncSize();
      applyMotionPreference();
    }).catch(() => { /* silent fail — parent bg-black shows through */ });

    const ro = new ResizeObserver(syncSize);
    ro.observe(wrap);
    reducedMotion.addEventListener("change", applyMotionPreference);

    return () => {
      cancelled = true;
      ro.disconnect();
      reducedMotion.removeEventListener("change", applyMotionPreference);
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    };
  }, [src, cols, mediaType]);

  return (
    <div
      ref={wrapRef}
      className={className}
      style={{ position: "absolute", inset: 0, overflow: "hidden", ...style }}
    >
      <canvas ref={cvRef} style={{ display: "block", width: "100%", height: "100%" }} />
    </div>
  );
}
