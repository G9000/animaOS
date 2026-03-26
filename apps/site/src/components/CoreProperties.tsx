import { useState, useEffect } from "react";

const BAR_WIDTH = 20;

// Portable — copy progress bar filling
function PortableAnimation() {
  const [fill, setFill] = useState(0);
  const [done, setDone] = useState(false);

  useEffect(() => {
    const start = Date.now();
    const iv = setInterval(() => {
      const pct = Math.min(100, ((Date.now() - start) / 1400) * 100);
      setFill(pct);
      if (pct >= 100) { clearInterval(iv); setDone(true); }
    }, 16);
    return () => clearInterval(iv);
  }, []);

  const filled = Math.round((fill / 100) * BAR_WIDTH);
  const bar = "█".repeat(filled) + "░".repeat(BAR_WIDTH - filled);

  return (
    <div className="font-mono text-[10px] text-muted-foreground space-y-1 mt-4 pt-4 border-t border-border">
      <div className="text-muted-foreground/60">$ cp -r .anima/ /Volumes/USB/</div>
      <div>[<span className="text-foreground">{bar}</span>]</div>
      <div className={`text-foreground transition-opacity duration-300 ${done ? "opacity-100" : "opacity-0"}`}>
        ✓ same mind. new shell.
      </div>
    </div>
  );
}

// Owned — passphrase entry typing out then key derivation
function OwnedAnimation() {
  const PASS = "••••••••••••••••";
  const [passLen, setPassLen] = useState(0);
  const [showDerive, setShowDerive] = useState(false);
  const [showDone, setShowDone] = useState(false);

  useEffect(() => {
    let t1: ReturnType<typeof setInterval>;
    let t2: ReturnType<typeof setTimeout>;
    let t3: ReturnType<typeof setTimeout>;

    t1 = setInterval(() => {
      setPassLen((p) => {
        if (p >= PASS.length) { clearInterval(t1); return p; }
        return p + 1;
      });
    }, 60);

    t2 = setTimeout(() => setShowDerive(true), PASS.length * 60 + 300);
    t3 = setTimeout(() => setShowDone(true),   PASS.length * 60 + 1100);

    return () => { clearInterval(t1); clearTimeout(t2); clearTimeout(t3); };
  }, []);

  return (
    <div className="font-mono text-[10px] text-muted-foreground space-y-1 mt-4 pt-4 border-t border-border">
      <div className="text-muted-foreground/60">$ anima unlock</div>
      <div>
        Passphrase:{" "}
        <span className="text-foreground">{PASS.slice(0, passLen)}</span>
        {passLen < PASS.length && (
          <span className="inline-block w-[6px] h-[10px] bg-foreground/60 animate-cursor align-middle ml-0.5" />
        )}
      </div>
      <div className={`transition-opacity duration-200 ${showDerive ? "opacity-100" : "opacity-0"}`}>
        Deriving key ...........{" "}
        <span className={showDone ? "text-foreground" : ""}>{showDone ? "done." : ""}</span>
      </div>
      <div className={`text-foreground transition-opacity duration-300 ${showDone ? "opacity-100" : "opacity-0"}`}>
        ✓ yours. not theirs.
      </div>
    </div>
  );
}

// Mortal — text dissolves character by character in random order
function MortalAnimation() {
  const TEXT = "1,463 days of memory.";
  const [faded, setFaded] = useState<boolean[]>(Array(TEXT.length).fill(false));
  const [showGone, setShowGone] = useState(false);

  useEffect(() => {
    const indices = TEXT.split("").map((_, i) => i);
    for (let i = indices.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [indices[i], indices[j]] = [indices[j], indices[i]];
    }

    let step = 0;
    const iv = setInterval(() => {
      if (step >= indices.length) {
        clearInterval(iv);
        setTimeout(() => setShowGone(true), 200);
        return;
      }
      const idx = indices[step];
      setFaded((prev) => { const next = [...prev]; next[idx] = true; return next; });
      step++;
    }, 75);

    return () => clearInterval(iv);
  }, []);

  return (
    <div className="font-mono text-[10px] text-muted-foreground space-y-1 mt-4 pt-4 border-t border-border">
      <div className="text-muted-foreground/60">$ shred -u anima.db</div>
      <div>
        {TEXT.split("").map((ch, i) => (
          <span
            key={i}
            className="transition-opacity duration-150"
            style={{ opacity: faded[i] ? 0 : 1 }}
          >
            {ch}
          </span>
        ))}
      </div>
      <div className={`text-muted-foreground/40 transition-opacity duration-500 ${showGone ? "opacity-100" : "opacity-0"}`}>
        gone. permanently.
      </div>
    </div>
  );
}

type Card = "portable" | "owned" | "mortal";

export default function CoreProperties() {
  const [hovered, setHovered] = useState<Card | null>(null);

  return (
    <div className="flex flex-col gap-px border border-border">
      {(
        [
          {
            id: "portable" as Card,
            title: "Portable",
            body: "Copy the Core to a USB drive. Plug into a new machine. Enter the passphrase. The AI wakes up with everything intact. Same mind. New shell.",
            Animation: PortableAnimation,
            border: "border-b border-border",
          },
          {
            id: "owned" as Card,
            title: "Owned",
            body: "No cloud. No platform account. No company shutdown can erase the relationship. You own it the way you own a physical object.",
            Animation: OwnedAnimation,
            border: "border-b border-border",
          },
          {
            id: "mortal" as Card,
            title: "Mortal",
            body: "Lose the passphrase and the soul dies permanently. This is not a bug. A relationship that can always be restored isn't a relationship — it's a service.",
            Animation: MortalAnimation,
            border: "",
          },
        ] as const
      ).map(({ id, title, body, Animation, border }) => (
        <div
          key={id}
          className={`bg-card px-6 py-6 ${border} cursor-default select-none`}
          onMouseEnter={() => setHovered(id)}
          onMouseLeave={() => setHovered(null)}
        >
          <p className="font-mono text-[9px] tracking-[0.3em] uppercase text-foreground mb-3">
            {title}
          </p>
          <p className="font-sans text-sm text-muted-foreground leading-relaxed">{body}</p>
          {hovered === id && <Animation key={id} />}
        </div>
      ))}
    </div>
  );
}
