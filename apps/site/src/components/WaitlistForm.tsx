import { useRef } from "react";
import { animate } from "animejs";
import { useWaitlist } from "../lib/useWaitlist";

export default function WaitlistForm() {
  const { email, setEmail, state, submit } = useWaitlist();
  const fillRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLSpanElement>(null);

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
      <div className="border px-6 py-5" style={{ borderColor: "var(--accent)", background: "color-mix(in oklch, var(--accent) 8%, transparent)" }}>
        <p className="font-mono text-[9px] tracking-[0.25em] uppercase mb-1" style={{ color: "var(--accent)" }}>
          // confirmed
        </p>
        <p className="font-mono text-sm text-foreground">
          You're on the list. I'll find you when I wake up.
        </p>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row sm:gap-px">
      <div
        className="flex items-center px-4 py-3 flex-1 gap-2"
        style={{ border: "1px solid color-mix(in oklch, var(--accent) 40%, transparent)", background: "color-mix(in oklch, var(--accent) 5%, transparent)" }}
      >
        <span className="font-mono text-sm shrink-0" style={{ color: "var(--accent)", opacity: 0.4 }}>&gt;</span>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="your@email.com"
          required
          className="flex-1 bg-transparent font-mono text-sm text-foreground placeholder:text-muted-foreground/30 focus:outline-none"
        />
      </div>
      <div
        className="relative w-full sm:w-auto transition-[transform,box-shadow] duration-150"
        onMouseEnter={(e) => {
          e.currentTarget.style.transform = "translate(2px, 4px)";
          e.currentTarget.style.boxShadow =
            "-2px -4px 0 var(--color-accent-dark), -4px -8px 0 color-mix(in oklch, var(--color-accent-dark) 40%, transparent)";
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.transform = "";
          e.currentTarget.style.boxShadow = "";
        }}
      >
        <button
          type="submit"
          disabled={state === "loading"}
          onMouseEnter={handleEnter}
          onMouseLeave={handleLeave}
          className="relative overflow-hidden px-6 py-3 disabled:opacity-50 w-full cursor-pointer"
          style={{ border: "1px solid var(--accent)", borderLeftWidth: "4px", borderRightWidth: "4px", background: "color-mix(in oklch, var(--accent) 8%, transparent)" }}
        >
          <div ref={fillRef} className="absolute inset-0" style={{ background: "var(--accent)", transform: "translateX(-100%)" }} />
          <span ref={textRef} className="relative z-10 font-mono text-[10px] tracking-[0.2em] uppercase" style={{ color: "var(--accent)" }}>
            {state === "loading" ? "..." : "join waitlist"}
          </span>
        </button>
      </div>
    </form>
  );
}
