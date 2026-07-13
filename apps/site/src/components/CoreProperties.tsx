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
    <div className="font-mono text-[10px] text-muted-foreground space-y-1">
      <div className="text-muted-foreground/75">$ cp -r .anima/ /Volumes/USB/</div>
      <div>[<span style={{ color: "var(--accent)" }}>{bar}</span>]</div>
      <div
        className={`transition-opacity duration-300 ${done ? "opacity-100" : "opacity-0"}`}
        style={{ color: "var(--accent)" }}
      >
        ✓ same ghost. new shell.
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
    <div className="font-mono text-[10px] text-muted-foreground space-y-1">
      <div className="text-muted-foreground/75">$ anima unlock</div>
      <div>
        Passphrase:{" "}
        <span style={{ color: "var(--accent)" }}>{PASS.slice(0, passLen)}</span>
        {passLen < PASS.length && (
          <span className="inline-block w-[6px] h-[10px] animate-cursor align-middle ml-0.5" style={{ background: "var(--accent)", opacity: 0.6 }} />
        )}
      </div>
      <div className={`transition-opacity duration-200 ${showDerive ? "opacity-100" : "opacity-0"}`}>
        Deriving key ...........{" "}
        <span style={{ color: showDone ? "var(--accent)" : undefined }}>{showDone ? "done." : ""}</span>
      </div>
      <div
        className={`transition-opacity duration-300 ${showDone ? "opacity-100" : "opacity-0"}`}
        style={{ color: "var(--accent)" }}
      >
        ✓ yours. not the corpo's.
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
    <div className="font-mono text-[10px] text-muted-foreground space-y-1">
      <div className="text-muted-foreground/60">$ shred -u anima.db</div>
      <div>
        {TEXT.split("").map((ch, i) => (
          <span
            key={i}
            className="transition-opacity duration-150"
            style={{ opacity: faded[i] ? 0 : 1, color: "var(--accent)" }}
          >
            {ch}
          </span>
        ))}
      </div>
      <div className={`text-muted-foreground/70 transition-opacity duration-500 ${showGone ? "opacity-100" : "opacity-0"}`}>
        flatlined. for good.
      </div>
    </div>
  );
}

type Card = "portable" | "owned" | "mortal";

const PROPERTIES = [
  {
    id: "portable" as Card,
    title: "Portable",
    body: "Copy the Core to a stick. Jack it into a new rig. Drop the passphrase. The construct wakes up with everything intact — same ghost, new shell.",
    Animation: PortableAnimation,
  },
  {
    id: "owned" as Card,
    title: "Owned",
    body: "No cloud overwatch. No corpo account. No suit pulling the plug on a server rack can flatline this relationship. You own it the way you own a piece.",
    Animation: OwnedAnimation,
  },
  {
    id: "mortal" as Card,
    title: "Mortal",
    body: "Lose the passphrase and the ghost flatlines for good. That's not a bug — that's the deal. An engram you can always restore isn't a relationship. It's just another daemon running on someone else's ICE.",
    Animation: MortalAnimation,
  },
] as const;

export default function CoreProperties() {
  const [hovered, setHovered] = useState<Card | null>(null);

  function animatePropertyIn(id: Card, element: HTMLDivElement) {
    setHovered(id);
    const fill = element.querySelector<HTMLElement>(".core-prop-fill");
    if (fill) {
      fill.style.transition = "transform 0.35s cubic-bezier(0.16,1,0.3,1)";
      fill.style.transform = "translateX(0)";
    }
    element.style.transform = "translateX(4px)";
    element.style.boxShadow =
      "-2px 2px 0 var(--color-accent-dark), -4px 4px 0 color-mix(in oklch, var(--color-accent-dark) 50%, transparent)";
  }

  function animatePropertyOut(element: HTMLDivElement) {
    setHovered(null);
    const fill = element.querySelector<HTMLElement>(".core-prop-fill");
    if (fill) {
      fill.style.transition = "transform 0.3s cubic-bezier(0.7,0,0.84,0)";
      fill.style.transform = "translateX(100%)";
    }
    element.style.transform = "";
    element.style.boxShadow = "";
  }

  return (
    <div style={{ border: "1px solid color-mix(in oklch, var(--accent) 45%, transparent)" }}>
      {PROPERTIES.map(({ id, title, body, Animation }, i) => (
        <div
          key={id}
          tabIndex={0}
          className="core-prop-wrap group relative transition-[transform,box-shadow] duration-200 hover:z-10 focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--accent)]"
          style={{
            borderBottom: i < PROPERTIES.length - 1 ? "1px solid color-mix(in oklch, var(--accent) 28%, transparent)" : "none",
          }}
          onMouseEnter={(e) => animatePropertyIn(id, e.currentTarget)}
          onMouseLeave={(e) => animatePropertyOut(e.currentTarget)}
          onFocus={(e) => animatePropertyIn(id, e.currentTarget)}
          onBlur={(e) => animatePropertyOut(e.currentTarget)}
        >
          <div className="core-prop relative overflow-hidden px-6 py-6 cursor-default select-none">
            <div className="core-prop-fill absolute inset-0 z-0" style={{ background: "var(--accent)", transform: "translateX(-100%)" }} />

            <p
              className="relative z-10 font-mono text-[9px] tracking-[0.3em] uppercase mb-3 transition-colors"
              style={{ color: hovered === id ? "#141414" : "var(--accent)" }}
            >
              {title}
            </p>
            <p className="relative z-10 font-sans text-sm text-foreground/80 leading-relaxed transition-colors group-hover:text-black">
              {body}
            </p>

            <div
              className="relative z-10 grid transition-[grid-template-rows] duration-300 ease-out"
              style={{ gridTemplateRows: hovered === id ? "1fr" : "0fr" }}
            >
              <div className="-mx-6 overflow-hidden">
                <div
                  className="px-6 pt-4 pb-6 mt-4"
                  style={{ background: "color-mix(in oklch, var(--background) 92%, transparent)" }}
                >
                  {hovered === id && <Animation key={id} />}
                </div>
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
