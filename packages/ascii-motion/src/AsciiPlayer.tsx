import { useState, useRef, useCallback, useEffect } from "react";
import { demux } from "./demux.ts";
import { GSETS } from "./glyphs.ts";
import { paintFrame, fitCanvas, type FrameData, type RenderOptions } from "./renderer.ts";

const ACCENT = "#7CFF6B";
const MONO = '"SFMono-Regular",ui-monospace,Menlo,Consolas,monospace';

/* ── sub-components ── */

function Rng({ label, val, min, max, step, set, fmt, note }: {
  label: string; val: number; min: number; max: number; step: number;
  set: (v: number) => void; fmt?: (v: number) => string; note?: string;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
        <span style={S.lbl}>{label}</span>
        <span style={{ fontSize: 10, color: "#888", fontFamily: MONO }}>{fmt ? fmt(val) : val}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={val}
        onChange={e => set(+e.target.value)} style={S.sl} />
      {note && <div style={{ fontSize: 8, color: "#333", marginTop: -2 }}>{note}</div>}
    </div>
  );
}

function Ck({ checked, set, icon, label }: {
  checked: boolean; set: (v: boolean) => void; icon: string; label: string;
}) {
  return (
    <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 11, color: checked ? "#ccc" : "#666", transition: "color .15s" }}>
      <input type="checkbox" checked={checked} onChange={e => set(e.target.checked)}
        style={{ accentColor: ACCENT, width: 13, height: 13 }} />
      <span style={{ fontSize: 12 }}>{icon}</span>{label}
    </label>
  );
}

function Btn({ children, onClick, disabled }: {
  children: React.ReactNode; onClick?: () => void; disabled?: boolean;
}) {
  return (
    <button disabled={disabled} onClick={onClick} style={{
      flex: 1, background: "none", border: `1px solid ${disabled ? "#1a1a1a" : "#2a2a2a"}`,
      color: disabled ? "#333" : "#888", fontFamily: MONO, fontSize: 9, letterSpacing: "0.08em",
      textTransform: "uppercase", padding: "8px 4px", cursor: disabled ? "not-allowed" : "pointer",
      borderRadius: 3, transition: "all .15s",
    }}>{children}</button>
  );
}

/* ── decode helper ── */

async function decodeVideo(
  file: File,
  cols: number,
  onProgress: (pct: number, info: string) => void,
): Promise<FrameData> {
  if (!("VideoDecoder" in window)) throw new Error("WebCodecs not supported — use Chrome/Edge");

  onProgress(0, "Reading file…");
  const buf = await file.arrayBuffer();

  onProgress(0, "Parsing MP4…");
  const dm = demux(buf);

  onProgress(0, `Decoding ${dm.samples.length} frames…`);
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
        onProgress(Math.round(decoded.length / dm.samples.length * 100), "Decoding…");
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

/* ── main component ── */

export interface AsciiPlayerProps {
  defaultCols?: number;
  defaultGlyphSet?: string;
  defaultContrast?: number;
  defaultBrightness?: number;
}

export function AsciiPlayer({
  defaultCols = 100,
  defaultGlyphSet = "K·▪",
  defaultContrast = 1.1,
  defaultBrightness = 0,
}: AsciiPlayerProps) {
  type Phase = "idle" | "loading" | "ready";

  const [phase, setPhase] = useState<Phase>("idle");
  const [pct, setPct] = useState(0);
  const [info, setInfo] = useState("");
  const [data, setData] = useState<FrameData | null>(null);
  const [fi, setFi] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [loop, setLoop] = useState(true);
  const [speed, setSpeed] = useState(1);
  const [cols, setCols] = useState(defaultCols);
  const [contrast, setContrast] = useState(defaultContrast);
  const [brightness, setBrightness] = useState(defaultBrightness);
  const [glyphSet, setGlyphSet] = useState(defaultGlyphSet);
  const [invert, setInvert] = useState(false);
  const [color, setColor] = useState(false);
  const [edgeDetect, setEdgeDetect] = useState(false);
  const [drag, setDrag] = useState(false);
  const [customGlyph, setCustomGlyph] = useState("");

  const cvRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number>(0);
  const tRef = useRef(0);
  const fiRef = useRef(0);
  const pRef = useRef(false);
  const dRef = useRef<FrameData | null>(null);

  // mirror state into refs for rAF closure
  const spRef = useRef(speed);
  const loRef = useRef(loop);
  const optsRef = useRef<RenderOptions>({ glyphSet, contrast, brightness, invert, color, edgeDetect });

  useEffect(() => { dRef.current = data; }, [data]);
  useEffect(() => { pRef.current = playing; }, [playing]);
  useEffect(() => { spRef.current = speed; }, [speed]);
  useEffect(() => { loRef.current = loop; }, [loop]);
  useEffect(() => {
    optsRef.current = { glyphSet, contrast, brightness, invert, color, edgeDetect };
  }, [glyphSet, contrast, brightness, invert, color, edgeDetect]);

  const paint = useCallback((idx: number) => {
    const d = dRef.current;
    const cv = cvRef.current;
    if (!d || !cv) return;
    paintFrame(cv, d, idx, optsRef.current);
  }, []);

  const tick = useCallback((now: number) => {
    if (!pRef.current || !dRef.current) return;
    const d = dRef.current;
    const elapsed = (now - tRef.current) / 1000 * spRef.current;
    let idx = 0;
    for (let i = 0; i < d.ts.length; i++) { if (d.ts[i] <= elapsed) idx = i; else break; }
    if (idx !== fiRef.current) { fiRef.current = idx; setFi(idx); }
    paint(idx);
    if (elapsed >= d.duration) {
      if (loRef.current) { tRef.current = now; fiRef.current = 0; setFi(0); }
      else { setPlaying(false); return; }
    }
    rafRef.current = requestAnimationFrame(tick);
  }, [paint]);

  const doPlay = useCallback(() => {
    if (!dRef.current) return;
    tRef.current = performance.now() - (dRef.current.ts[fiRef.current] ?? 0) / spRef.current * 1000;
    setPlaying(true);
    rafRef.current = requestAnimationFrame(tick);
  }, [tick]);

  const doPause = useCallback(() => {
    setPlaying(false);
    if (rafRef.current) cancelAnimationFrame(rafRef.current);
  }, []);

  const doSeek = useCallback((idx: number) => {
    fiRef.current = idx;
    setFi(idx);
    paint(idx);
    if (pRef.current && dRef.current) {
      tRef.current = performance.now() - (dRef.current.ts[idx] ?? 0) / spRef.current * 1000;
    }
  }, [paint]);

  const doStep = useCallback((dir: number) => {
    if (!dRef.current) return;
    doPause();
    doSeek(Math.max(0, Math.min(dRef.current.count - 1, fiRef.current + dir)));
  }, [doPause, doSeek]);

  // repaint when settings change while paused
  useEffect(() => { if (data && !playing) paint(fiRef.current); }, [data, glyphSet, contrast, brightness, invert, color, edgeDetect, playing, paint]);

  // resize canvas
  const doFitCanvas = useCallback(() => {
    const d = dRef.current;
    const cv = cvRef.current;
    const stage = stageRef.current;
    if (!d || !cv || !stage) return;
    fitCanvas(cv, stage, d);
    paint(fiRef.current);
  }, [paint]);

  useEffect(doFitCanvas, [doFitCanvas]);
  useEffect(() => {
    window.addEventListener("resize", doFitCanvas);
    return () => window.removeEventListener("resize", doFitCanvas);
  }, [doFitCanvas]);

  const load = useCallback(async (file: File | undefined | null) => {
    if (!file?.type.startsWith("video")) return;
    doPause(); setData(null); setPhase("loading"); setPct(0); setFi(0); fiRef.current = 0;
    try {
      const fd = await decodeVideo(file, cols, (p, msg) => { setPct(p); setInfo(msg); });
      setData(fd);
      setPhase("ready");
      setInfo(`${fd.count} frames · ${fd.srcW}×${fd.srcH} → ${fd.w}×${fd.h} · ${fd.duration.toFixed(1)}s`);
      setTimeout(() => {
        dRef.current = fd; fiRef.current = 0;
        paint(0);
        tRef.current = performance.now();
        setPlaying(true);
        rafRef.current = requestAnimationFrame(tick);
      }, 200);
    } catch (e) {
      setPhase("idle");
      setInfo("Error: " + (e instanceof Error ? e.message : String(e)));
    }
  }, [cols, doPause, paint, tick]);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); setDrag(false);
    load(e.dataTransfer?.files?.[0]);
  }, [load]);

  const exportPng = useCallback(() => {
    if (!cvRef.current) return;
    const a = document.createElement("a");
    a.download = `ascii-f${fi}.png`;
    a.href = cvRef.current.toDataURL("image/png");
    a.click();
  }, [fi]);

  // keyboard shortcuts
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT") return;
      if (e.code === "Space") { e.preventDefault(); playing ? doPause() : doPlay(); }
      if (e.code === "ArrowRight") doStep(1);
      if (e.code === "ArrowLeft") doStep(-1);
      if (e.code === "BracketRight") setSpeed(s => Math.min(4, +(s + 0.25).toFixed(2)));
      if (e.code === "BracketLeft") setSpeed(s => Math.max(0.25, +(s - 0.25).toFixed(2)));
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [playing, doPause, doPlay, doStep]);

  const fmt = (s: number) => { const m = Math.floor(s / 60); return `${m}:${(s % 60).toFixed(1).padStart(4, "0")}`; };
  const curTime = data ? data.ts[fi] ?? 0 : 0;

  return (
    <div style={S.root}>
      {/* header */}
      <div style={S.head}>
        <span style={S.logo}>ASCII<span style={{ color: ACCENT }}>·</span>MOTION</span>
        <span style={{ fontSize: 10, color: "#3a3a3a", letterSpacing: "0.06em" }}>video → living text</span>
        <div style={{ flex: 1 }} />
        {data && (
          <span style={{ fontSize: 10, color: "#3a3a3a" }}>
            {data.srcW}×{data.srcH} → {data.w}×{data.h} · {data.count}f · {(data.count / data.duration).toFixed(0)}fps
          </span>
        )}
      </div>

      <div style={S.body}>
        {/* stage */}
        <div ref={stageRef} style={S.stage}
          onDragOver={e => { e.preventDefault(); setDrag(true); }}
          onDragLeave={() => setDrag(false)}
          onDrop={onDrop}>
          {phase !== "ready" ? (
            <label style={{ ...S.drop, borderColor: drag ? ACCENT : "#1e1e1e", color: drag ? "#bbb" : "#444" }}>
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="0.8">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                <polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
              </svg>
              {phase === "loading" ? (
                <>
                  <div style={{ fontSize: 12, letterSpacing: "0.1em", textTransform: "uppercase" }}>Decoding — {pct}%</div>
                  <div style={S.bar}><div style={{ ...S.barFill, width: `${pct}%` }} /></div>
                  <div style={{ fontSize: 10, opacity: 0.5 }}>{info}</div>
                </>
              ) : (
                <>
                  <div style={{ fontSize: 13, letterSpacing: "0.12em", textTransform: "uppercase" }}>Drop video here</div>
                  <div style={{ fontSize: 11, maxWidth: 340, lineHeight: 1.7, opacity: 0.6 }}>
                    or click to browse. Runs entirely in your browser. MP4 (H.264) works best.
                  </div>
                  {info && <div style={{ fontSize: 10, color: "#c44", marginTop: 6 }}>{info}</div>}
                </>
              )}
              <input type="file" accept="video/mp4,video/*" style={{ display: "none" }}
                onChange={e => load(e.target.files?.[0])} />
            </label>
          ) : (
            <canvas ref={cvRef} style={{ maxWidth: "100%", maxHeight: "100%", display: "block", imageRendering: "auto" }} />
          )}
        </div>

        {/* sidebar */}
        <div style={S.side}>
          <div style={S.sideTitle}>Controls</div>

          <Rng label="Resolution" val={cols} min={30} max={180} step={2} set={setCols} note="re-decode needed" />
          <Rng label="Contrast" val={contrast} min={0.3} max={3} step={0.05} set={setContrast} fmt={v => v.toFixed(2)} />
          <Rng label="Brightness" val={brightness} min={-100} max={100} step={2} set={setBrightness} />
          <Rng label="Speed" val={speed} min={0.25} max={4} step={0.25} set={setSpeed} fmt={v => `${v}×`} />

          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
              <span style={S.lbl}>Glyph set</span>
              <span style={{ fontSize: 9, color: "#555" }}>{Object.keys(GSETS).length} sets</span>
            </div>
            <div style={{ maxHeight: 180, overflowY: "auto", border: "1px solid #1c1c1c", borderRadius: 3, background: "#0a0a0a" }}>
              {Object.keys(GSETS).map(k => (
                <button key={k} onClick={() => setGlyphSet(k)} style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  width: "100%", background: glyphSet === k ? "#151515" : "transparent",
                  border: "none", borderBottom: "1px solid #131313", padding: "6px 8px",
                  cursor: "pointer", fontFamily: MONO, fontSize: 10, textAlign: "left",
                  transition: "background .1s", color: glyphSet === k ? ACCENT : "#666",
                }}>
                  <span style={{ fontWeight: glyphSet === k ? 700 : 400 }}>{k}</span>
                  <span style={{ fontSize: 9, opacity: 0.5, maxWidth: 100, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {GSETS[k].slice(1, 8).join("")}
                  </span>
                </button>
              ))}
            </div>
            <input
              type="text"
              placeholder="Custom: type chars dark→light"
              value={customGlyph}
              onChange={e => {
                setCustomGlyph(e.target.value);
                if (e.target.value.length >= 2) {
                  GSETS["Custom"] = [" ", ...e.target.value.split("")];
                  setGlyphSet("Custom");
                }
              }}
              style={{ background: "#0c0c0c", border: "1px solid #1c1c1c", borderRadius: 3, padding: "6px 8px", color: "#aaa", fontFamily: MONO, fontSize: 10, outline: "none", width: "100%", boxSizing: "border-box" }}
            />
            <div style={{ fontSize: 9, color: "#333", letterSpacing: 1, wordBreak: "break-all", lineHeight: 1.8 }}>
              <span style={{ color: "#444" }}>Active:</span> {(GSETS[glyphSet] ?? []).slice(1).join(" ")}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 9, marginTop: 2 }}>
            <Ck checked={color} set={setColor} icon="🎨" label="Color mode" />
            <Ck checked={edgeDetect} set={setEdgeDetect} icon="◈" label="Edge detect" />
            <Ck checked={invert} set={setInvert} icon="◐" label="Invert" />
            <Ck checked={loop} set={setLoop} icon="↻" label="Loop playback" />
          </div>

          <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
            <Btn onClick={exportPng} disabled={!data}>Save PNG</Btn>
            <Btn onClick={() => { const i = document.createElement("input"); i.type = "file"; i.accept = "video/mp4,video/*"; i.onchange = (e) => load((e.target as HTMLInputElement).files?.[0]); i.click(); }}>Load new</Btn>
          </div>

          <div style={{ marginTop: "auto", fontSize: 9, color: "#333", lineHeight: 2 }}>
            <span style={{ color: "#555" }}>Shortcuts</span><br />
            Space — play / pause<br />
            ← → — step frame<br />
            [ ] — adjust speed<br />
          </div>
        </div>
      </div>

      {/* transport */}
      {data && (
        <div style={S.trans}>
          <button style={S.tBtn} onClick={() => doStep(-1)} title="Previous frame">⏮</button>
          <button style={{ ...S.tBtn, fontSize: 15, width: 32 }} onClick={() => playing ? doPause() : doPlay()}>{playing ? "⏸" : "▶"}</button>
          <button style={S.tBtn} onClick={() => doStep(1)} title="Next frame">⏭</button>
          <span style={S.tm}>{fmt(curTime)}</span>
          <input type="range" min={0} max={data.count - 1} value={fi}
            onChange={e => doSeek(+e.target.value)}
            style={{ flex: 1, height: 4, accentColor: ACCENT, background: "#181818", WebkitAppearance: "none", appearance: "none", borderRadius: 2, cursor: "pointer", outline: "none" }} />
          <span style={S.tm}>{fmt(data.duration)}</span>
          <span style={{ fontSize: 10, color: ACCENT, marginLeft: 6, minWidth: 32, textAlign: "right", fontWeight: 600 }}>{speed}×</span>
        </div>
      )}
    </div>
  );
}

const S: Record<string, React.CSSProperties> = {
  root:      { display: "flex", flexDirection: "column", height: "100vh", background: "#050505", color: "#ccc", fontFamily: MONO, overflow: "hidden" },
  head:      { padding: "12px 20px 10px", borderBottom: "1px solid #131313", display: "flex", alignItems: "baseline", gap: 14, flexShrink: 0 },
  logo:      { fontSize: 14, fontWeight: 700, letterSpacing: "0.16em", textTransform: "uppercase" },
  body:      { display: "flex", flex: 1, minHeight: 0 },
  stage:     { flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 12, overflow: "hidden", position: "relative", minWidth: 0 },
  drop:      { position: "absolute", inset: 16, border: "1px dashed #1e1e1e", borderRadius: 6, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 12, cursor: "pointer", textAlign: "center", transition: "all .2s" },
  bar:       { width: 240, height: 4, background: "#141414", borderRadius: 3, marginTop: 4 },
  barFill:   { height: "100%", background: ACCENT, borderRadius: 3, transition: "width .15s" },
  side:      { width: 240, borderLeft: "1px solid #131313", padding: "16px 14px", display: "flex", flexDirection: "column", gap: 14, overflowY: "auto", background: "#080808", flexShrink: 0 },
  sideTitle: { fontSize: 9, letterSpacing: "0.18em", textTransform: "uppercase", color: "#333", paddingBottom: 4, borderBottom: "1px solid #151515" },
  lbl:       { fontSize: 9, letterSpacing: "0.12em", textTransform: "uppercase", color: "#4a4a4a" },
  sl:        { WebkitAppearance: "none", appearance: "none", width: "100%", height: 2, background: "#1a1a1a", outline: "none", accentColor: ACCENT, borderRadius: 1, cursor: "pointer" },
  trans:     { display: "flex", alignItems: "center", gap: 8, padding: "8px 20px", borderTop: "1px solid #131313", background: "#080808", flexShrink: 0 },
  tBtn:      { background: "none", border: "none", color: "#777", fontSize: 12, cursor: "pointer", padding: "2px 3px", fontFamily: MONO, width: 28, textAlign: "center" },
  tm:        { color: "#444", fontSize: 10, fontFamily: MONO, minWidth: 38, letterSpacing: "0.02em" },
};
