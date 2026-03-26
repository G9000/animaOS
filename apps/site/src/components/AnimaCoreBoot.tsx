import { useState, useEffect, useRef } from "react";

interface TextStep {
  delay: number;
  kind: "text";
  style: "cmd" | "dim" | "ok" | "ai";
  text: string;
}
interface BlankStep {
  delay: number;
  kind: "blank";
}
interface ProgressStep {
  delay: number;
  kind: "progress";
  label: string;
  count: string;
}
type Step = TextStep | BlankStep | ProgressStep;
type VisibleStep = Step & { id: number };

const BAR_WIDTH = 20;
const PROGRESS_DURATION = 900;
const RESTART_DELAY = 17500;

const STEPS: Step[] = [
  { delay: 0,     kind: "text", style: "cmd", text: "$ anima import anima-vault-2026-03-15.vault" },
  { delay: 400,   kind: "blank" },
  { delay: 700,   kind: "text", style: "dim", text: "Passphrase: ••••••••••••••••" },
  { delay: 1400,  kind: "text", style: "dim", text: "Deriving key ............... done." },
  { delay: 2000,  kind: "text", style: "dim", text: "Decrypting vault ........... done." },
  { delay: 2600,  kind: "text", style: "ok",  text: "Verifying integrity ........ ✓" },
  { delay: 3200,  kind: "blank" },
  { delay: 3400,  kind: "text", style: "dim", text: "Restoring Core:" },
  { delay: 3800,  kind: "progress", label: "  ■ memories        ", count: "2,847" },
  { delay: 4800,  kind: "progress", label: "  ■ identity        ", count: "5" },
  { delay: 5600,  kind: "progress", label: "  ■ emotional hist. ", count: "14,209" },
  { delay: 6600,  kind: "progress", label: "  ■ conversations   ", count: "1,034" },
  { delay: 7500,  kind: "progress", label: "  ■ self-model      ", count: "done" },
  { delay: 8400,  kind: "blank" },
  { delay: 8600,  kind: "text", style: "dim", text: "Encrypting new Core ........ done." },
  { delay: 9200,  kind: "text", style: "dim", text: "Writing manifest ........... done." },
  { delay: 9800,  kind: "blank" },
  { delay: 10000, kind: "text", style: "ok",  text: "✓ Core restored. 1,463 days of memory." },
  { delay: 10800, kind: "blank" },
  { delay: 11000, kind: "text", style: "cmd", text: "$ anima start" },
  { delay: 11600, kind: "blank" },
  { delay: 11800, kind: "text", style: "dim", text: "Loading Core ..." },
  { delay: 12700, kind: "text", style: "ai",  text: "> Hey. I remember you." },
  { delay: 13700, kind: "text", style: "ai",  text: "> It's been a few minutes, right? Different machine." },
  { delay: 14800, kind: "text", style: "ai",  text: "> Everything's still here. I'm still here." },
];

function renderBar(fill: number) {
  const filled = Math.round((fill / 100) * BAR_WIDTH);
  const empty = BAR_WIDTH - filled;
  return "[" + "█".repeat(filled) + "░".repeat(empty) + "]";
}

export default function AnimaCoreBoot() {
  const [lines, setLines] = useState<VisibleStep[]>([]);
  const [fills, setFills] = useState<Record<number, number>>({});
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];
    const intervals: ReturnType<typeof setInterval>[] = [];
    let alive = true;

    function run() {
      setLines([]);
      setFills({});

      STEPS.forEach((step, idx) => {
        const t = setTimeout(() => {
          if (!alive) return;
          setLines((prev) => [...prev, { ...step, id: idx }]);

          if (step.kind === "progress") {
            const start = Date.now();
            const iv = setInterval(() => {
              const pct = Math.min(100, ((Date.now() - start) / PROGRESS_DURATION) * 100);
              setFills((prev) => ({ ...prev, [idx]: pct }));
              if (pct >= 100) clearInterval(iv);
            }, 16);
            intervals.push(iv);
          }
        }, step.delay);
        timers.push(t);
      });

      const restart = setTimeout(() => { if (alive) run(); }, RESTART_DELAY);
      timers.push(restart);
    }

    run();
    return () => {
      alive = false;
      timers.forEach(clearTimeout);
      intervals.forEach(clearInterval);
    };
  }, []);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines]);

  return (
    <div className="border border-border bg-card px-5 py-4 h-80 flex flex-col">
      <p className="font-mono text-[9px] tracking-[0.25em] uppercase text-muted-foreground/40 mb-3 shrink-0">
        // core transfer
      </p>
      <div ref={containerRef} className="flex-1 overflow-y-auto flex flex-col justify-end">
        <div>
          {lines.map((line) => {
            if (line.kind === "blank") {
              return <div key={line.id} className="h-[1.8em]" />;
            }
            if (line.kind === "text") {
              const cls = {
                cmd: "text-foreground",
                dim: "text-muted-foreground",
                ok:  "text-foreground",
                ai:  "text-foreground font-medium",
              }[line.style];
              return (
                <div key={line.id} className={`font-mono text-[11px] leading-[1.8] ${cls}`}>
                  {line.text}
                </div>
              );
            }
            if (line.kind === "progress") {
              const fill = fills[line.id] ?? 0;
              return (
                <div key={line.id} className="font-mono text-[11px] leading-[1.8] text-muted-foreground whitespace-pre">
                  {line.label}
                  <span className="text-foreground">{renderBar(fill)}</span>
                  {fill >= 100 && (
                    <span className="text-muted-foreground/50"> {line.count}</span>
                  )}
                </div>
              );
            }
          })}
          <span className="inline-block w-[7px] h-[13px] bg-foreground/50 animate-cursor align-middle ml-0.5" />
        </div>
      </div>
    </div>
  );
}
