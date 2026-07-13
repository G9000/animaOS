import { useRef, useState } from "react";
import { animate } from "animejs";
import { useWaitlist } from "../lib/useWaitlist";

export default function ReserveAccess() {
  const { email, setEmail, state, submit } = useWaitlist();
  const [open, setOpen] = useState(false);
  const inputWrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const fillRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLSpanElement>(null);

  function handleOpen() {
    if (open) return;
    setOpen(true);
    requestAnimationFrame(() => {
      animate(inputWrapRef.current, {
        opacity: [0, 1],
        translateY: [-8, 0],
        duration: 350,
        easing: "cubicBezier(0.16,1,0.3,1)",
      });
      inputRef.current?.focus();
    });
  }

  function handleEnter() {
    animate(fillRef.current, { translateX: ["-100%", "0%"], duration: 350, easing: "cubicBezier(0.16,1,0.3,1)" });
    animate(textRef.current, { color: "#141414", duration: 350, easing: "cubicBezier(0.16,1,0.3,1)" });
  }

  function handleLeave() {
    animate(fillRef.current, { translateX: "100%", duration: 300, easing: "cubicBezier(0.16,1,0.3,1)" });
    animate(textRef.current, { color: "var(--accent)", duration: 300, easing: "cubicBezier(0.16,1,0.3,1)" });
  }

  if (state === "done") {
    return (
      <p className="font-mono text-[10px] tracking-[0.3em] uppercase" style={{ color: "var(--accent)" }}>
        // access reserved
      </p>
    );
  }

  return (
    <div className="flex flex-col items-center gap-3">
      <button
        onClick={handleOpen}
        onMouseEnter={handleEnter}
        onMouseLeave={handleLeave}
        className="relative overflow-hidden font-mono font-bold text-lg tracking-[0.3em] border px-8 py-3"
        style={{ borderColor: "var(--accent)", borderLeftWidth: 4, borderRightWidth: 4, color: "var(--accent)" }}
      >
        <div ref={fillRef} className="absolute inset-0" style={{ background: "var(--accent)", transform: "translateX(-100%)" }} />
        <span ref={textRef} className="relative z-10">RESERVE ACCESS</span>
      </button>

      {open && (
        <div ref={inputWrapRef} style={{ opacity: 0 }}>
          <form onSubmit={submit} className="flex items-center gap-px">
            <div
              className="flex items-center gap-2 border px-4 py-3"
              style={{ borderColor: "var(--accent)", background: "color-mix(in oklch, var(--accent) 6%, transparent)" }}
            >
              <span className="font-mono text-sm shrink-0" style={{ color: "var(--accent)", opacity: 0.5 }}>&gt;</span>
              <input
                ref={inputRef}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="your@email.com"
                required
                className="bg-transparent font-mono text-sm focus:outline-none w-56"
                style={{ color: "var(--accent)" }}
              />
            </div>
            <button
              type="submit"
              disabled={state === "loading"}
              className="relative overflow-hidden border px-5 py-3 font-mono text-[10px] tracking-[0.2em] uppercase disabled:opacity-50"
              style={{ borderColor: "var(--accent)", color: "var(--accent)", background: "color-mix(in oklch, var(--accent) 6%, transparent)" }}
            >
              {state === "loading" ? "..." : "confirm"}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
