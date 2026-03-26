import { useState, useEffect, useRef } from "react";
import { LOGO_SVG_PATH, DENSITY, hash } from "@anima/standard-templates";

// ─── Canvas / symbol layout ───────────────────────────────────────────────────
const CW = 60, CH = 27;
const SW = 30, SH = 15;
const SX = Math.floor((CW - SW) / 2); // 15
const SY = Math.floor((CH - SH) / 2); // 6
const MW = 8, MH = 4;

// 6 mini-symbol cluster centres (corners + top/bottom midpoints)
const CLUSTERS = [
  { x: 3,  y: 2,  delay: 0.00 },
  { x: 30, y: 0,  delay: 0.15 },
  { x: 56, y: 2,  delay: 0.30 },
  { x: 3,  y: 24, delay: 0.08 },
  { x: 30, y: 26, delay: 0.22 },
  { x: 56, y: 24, delay: 0.38 },
];

// ─── Phase config ─────────────────────────────────────────────────────────────
const PH_SHIMMER  = 0; // mini-symbols glow at clusters
const PH_BUILD    = 1; // staggered curved launch → big symbol
const PH_HOLD     = 2; // full shimmer + sparkle halo
const PH_DISSOLVE = 3; // reverse: big → mini clusters
const PH_GAP      = 4;
const PH_LEN = [50, 70, 55, 42, 10];

const SPARKLES = [".", ":", "*", "+", "°", "·"];

// ─── Helpers ──────────────────────────────────────────────────────────────────
const eio  = (t: number) => t < 0.5 ? 2*t*t : -1+(4-2*t)*t;
const eout = (t: number) => t*(2-t);

/** Quadratic bezier — curves right of the start→target midpoint (clockwise spiral) */
function bezier(t: number, sx: number, sy: number, tx: number, ty: number, curve: number) {
  const mx = (sx + tx) / 2, my = (sy + ty) / 2;
  const dx = tx - sx, dy = ty - sy;
  const len = Math.sqrt(dx*dx + dy*dy) || 1;
  // Perpendicular right of travel direction (clockwise)
  const cpx = mx + (dy / len) * curve;
  const cpy = my + (-dx / len) * curve;
  const mt = 1 - t;
  return { x: mt*mt*sx + 2*mt*t*cpx + t*t*tx,
           y: mt*mt*sy + 2*mt*t*cpy + t*t*ty };
}

function rasterize(path: string, cols: number, rows: number) {
  const W = 160, H = 164;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const ctx = c.getContext("2d")!;
  ctx.scale(W / 36, H / 37);
  ctx.fillStyle = "white";
  ctx.fill(new Path2D(path));
  const img = ctx.getImageData(0, 0, W, H);
  const cw = W / cols, ch = H / rows;
  const out: number[][] = [];
  for (let r = 0; r < rows; r++) {
    out[r] = [];
    for (let col = 0; col < cols; col++) {
      let sum = 0, cnt = 0;
      for (let py = Math.floor(r * ch); py < Math.min(H, Math.ceil((r+1)*ch)); py++)
        for (let px = Math.floor(col * cw); px < Math.min(W, Math.ceil((col+1)*cw)); px++) {
          sum += img.data[(py * W + px) * 4 + 3]; cnt++;
        }
      out[r][col] = cnt ? sum / cnt / 255 : 0;
    }
  }
  return out;
}

type Particle = {
  tx: number; ty: number;
  sx: number; sy: number;
  cluster: number;
  curve: number;   // bezier curvature amount (chars), unique per particle
  char: string;
};

// ─── Component ────────────────────────────────────────────────────────────────
export default function AnimaBuild() {
  const [frame, setFrame] = useState("");
  const bigAlpha  = useRef<number[][]>([]);
  const miniAlpha = useRef<number[][]>([]);
  const parts     = useRef<Particle[]>([]);
  const ready     = useRef(false);
  const t         = useRef(0);
  const phase     = useRef(PH_SHIMMER);
  const pf        = useRef(0);

  useEffect(() => {
    const big  = rasterize(LOGO_SVG_PATH, SW, SH);
    const mini = rasterize(LOGO_SVG_PATH, MW, MH);
    bigAlpha.current  = big;
    miniAlpha.current = mini;

    const particles: Particle[] = [];
    for (let r = 0; r < SH; r++)
      for (let c = 0; c < SW; c++) {
        const a = big[r][c];
        if (a > 0.08) particles.push({
          tx: SX + c, ty: SY + r,
          sx: 0, sy: 0,
          cluster: 0,
          curve: 0,
          char: DENSITY[Math.round(a * (DENSITY.length - 1))],
        });
      }

    // Shuffle (deterministic) so clusters get spatial mix
    for (let i = particles.length - 1; i > 0; i--) {
      const j = Math.floor(hash(i, 9, 13) * (i + 1));
      [particles[i], particles[j]] = [particles[j], particles[i]];
    }

    // Mini-symbol visible slots (for cluster shape)
    const slots: { lx: number; ly: number }[] = [];
    for (let r = 0; r < MH; r++)
      for (let c = 0; c < MW; c++)
        if (mini[r][c] > 0.08)
          slots.push({ lx: c - Math.floor(MW / 2), ly: r - Math.floor(MH / 2) });

    const perCluster = Math.ceil(particles.length / CLUSTERS.length);
    particles.forEach((p, i) => {
      const ci = Math.min(CLUSTERS.length - 1, Math.floor(i / perCluster));
      const { x: cx, y: cy } = CLUSTERS[ci];
      const li = i % slots.length;
      p.sx = Math.max(0, Math.min(CW - 1, cx + slots[li].lx));
      p.sy = Math.max(0, Math.min(CH - 1, cy + slots[li].ly));
      p.cluster = ci;
      // Per-particle curve variation: base 7 ± 4, different per particle
      p.curve = 7 + (hash(i, 2, 0) * 8 - 4);
    });

    parts.current = particles;
    ready.current = true;
  }, []);

  useEffect(() => {
    const tick = () => {
      if (!ready.current) return;
      const big  = bigAlpha.current;
      const mini = miniAlpha.current;
      const ps   = parts.current;
      const tick = t.current;
      const ph   = phase.current;
      const prog = Math.min(1, pf.current / PH_LEN[ph]);

      const grid = new Array<string>(CW * CH).fill(" ");
      const put  = (x: number, y: number, ch: string) => {
        if (x >= 0 && x < CW && y >= 0 && y < CH) grid[y * CW + x] = ch;
      };

      // ── SHIMMER: mini symbols glow at clusters, appear staggered ─────────
      if (ph === PH_SHIMMER) {
        CLUSTERS.forEach(({ x: cx, y: cy }, ci) => {
          // Each cluster fades in 1/6 of the way through the phase
          const appear = Math.max(0, Math.min(1, (prog - ci / 7) / (1 / 7)));
          if (appear <= 0) return;

          for (let mr = 0; mr < MH; mr++)
            for (let mc = 0; mc < MW; mc++) {
              const a = mini[mr][mc];
              if (a < 0.08) continue;
              const gx = cx - Math.floor(MW / 2) + mc;
              const gy = cy - Math.floor(MH / 2) + mr;
              const w = Math.sin(mc * 0.5 - tick * 0.1 + mr * 0.4) * 0.5 + 0.5;
              const pulse = Math.sin(tick * 0.07 + ci * 1.1) * 0.15 + 0.85;
              const b = a * (0.45 + 0.55 * w) * pulse * eout(appear);
              put(gx, gy, DENSITY[Math.max(0, Math.round(b * (DENSITY.length - 1)))]);
            }
        });
      }

      // ── BUILD / DISSOLVE: particles arc along curved bezier paths ─────────
      if (ph === PH_BUILD || ph === PH_DISSOLVE) {
        const fwd = ph === PH_BUILD;

        // Render fading mini symbols while particles are still launching
        if (fwd) {
          CLUSTERS.forEach(({ x: cx, y: cy, delay }, ci) => {
            const clusterProgress = Math.max(0, (prog - delay) / 0.15);
            const fade = Math.max(0, 1 - clusterProgress);
            if (fade <= 0) return;
            for (let mr = 0; mr < MH; mr++)
              for (let mc = 0; mc < MW; mc++) {
                const a = mini[mr][mc];
                if (a < 0.08) continue;
                const gx = cx - Math.floor(MW / 2) + mc;
                const gy = cy - Math.floor(MH / 2) + mr;
                const w = Math.sin(mc * 0.5 - tick * 0.1 + mr * 0.4) * 0.5 + 0.5;
                const b = a * (0.45 + 0.55 * w) * fade;
                put(gx, gy, DENSITY[Math.max(0, Math.round(b * (DENSITY.length - 1)))]);
              }
          });
        }

        for (const p of ps) {
          const { delay } = CLUSTERS[p.cluster];
          const raw = fwd
            ? Math.max(0, (prog - delay) / (1 - delay))
            : Math.max(0, (prog - (0.38 - delay)) / (1 - (0.38 - delay)));
          const localT = fwd ? eio(Math.min(1, raw)) : eout(Math.min(1, raw));

          let pos;
          if (fwd) {
            pos = bezier(localT, p.sx, p.sy, p.tx, p.ty, p.curve);
          } else {
            pos = bezier(localT, p.tx, p.ty, p.sx, p.sy, -p.curve);
          }

          const cx = Math.round(pos.x), cy = Math.round(pos.y);
          if (cx < 0 || cx >= CW || cy < 0 || cy >= CH) continue;

          const dx = (p.tx - cx) / SW, dy = (p.ty - cy) / SH;
          const dist = Math.sqrt(dx*dx + dy*dy);
          const fc = dist > 0.45 ? "·"
                   : dist > 0.25 ? ":"
                   : dist > 0.1  ? "-"
                   : p.char;

          const idx = cy * CW + cx;
          if (grid[idx] === " ") grid[idx] = fc;
        }
      }

      // ── HOLD: full shimmer + rotating sparkle halo ───────────────────────
      if (ph === PH_HOLD) {
        for (let fy = 0; fy < CH; fy++)
          for (let fx = 0; fx < CW; fx++) {
            const ac = fx - SX, ar = fy - SY;
            const a = (ac >= 0 && ac < SW && ar >= 0 && ar < SH) ? big[ar][ac] : 0;
            if (a > 0.08) {
              const w1 = Math.sin(fx * 0.2 - tick * 0.08 + fy * 0.15) * 0.5 + 0.5;
              const w2 = Math.sin(fx * 0.1 + tick * 0.12 - fy * 0.30) * 0.5 + 0.5;
              const w3 = Math.cos((fx + fy) * 0.13 + tick * 0.04   ) * 0.5 + 0.5;
              const pulse = Math.sin(tick * 0.025) * 0.12 + 0.88;
              const flick = hash(fx, fy, tick % 47) > 0.93 ? 0.2 : 0;
              const b = Math.min(1, a * (0.5 + 0.5*(w1*.4+w2*.35+w3*.25)) * pulse + flick);
              put(fx, fy, DENSITY[Math.round(b * (DENSITY.length - 1))]);
            } else {
              const ccx = CW / 2, ccy = CH / 2;
              const dx = (fx - ccx) / ccx, dy = (fy - ccy) / ccy;
              const dist = Math.sqrt(dx*dx + dy*dy);
              const ang = Math.atan2(dy, dx);
              const petals = Math.cos((ang - tick * 0.03) * 6) * 0.5 + 0.5;
              const bloom  = Math.sin(tick * 0.04) * 0.15 + 0.7;
              const ring   = 1 - Math.abs(dist - bloom) * 3;
              const inten  = Math.max(0, ring) * petals;
              const rng    = hash(fx, fy, Math.floor(tick * 0.25));
              if (inten > 0.5 && rng > 0.6)
                put(fx, fy, SPARKLES[Math.floor(hash(fx, fy, tick % 31) * SPARKLES.length)]);
            }
          }
      }

      // Stringify
      let out = "";
      for (let r = 0; r < CH; r++)
        out += grid.slice(r * CW, (r + 1) * CW).join("") + "\n";

      setFrame(out);
      t.current += 1;
      pf.current += 1;
      if (pf.current >= PH_LEN[ph]) {
        phase.current = (ph + 1) % 5;
        pf.current = 0;
      }
    };

    const id = setInterval(tick, 55);
    return () => clearInterval(id);
  }, []);

  return (
    <div className="relative select-none" aria-hidden="true">
      <pre className="absolute inset-0 font-mono text-[10px] sm:text-[12px] leading-[1.2] text-foreground/[0.06] whitespace-pre blur-[8px]">
        {frame}
      </pre>
      <pre className="relative font-mono text-[10px] sm:text-[12px] leading-[1.2] text-muted-foreground/50 whitespace-pre">
        {frame}
      </pre>
    </div>
  );
}
