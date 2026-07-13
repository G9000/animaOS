import { useRef } from "react";
import { animate, utils } from "animejs";

export default function AnimaLogo() {
  const frameRef = useRef<HTMLDivElement>(null);
  const fillRef = useRef<HTMLDivElement>(null);
  const textRef = useRef<HTMLSpanElement>(null);

  function handleEnter() {
    const frame = frameRef.current;
    const fill = fillRef.current;
    const text = textRef.current;
    if (!frame || !fill || !text) return;
    utils.remove([frame, fill, text]);

    animate(frame, {
      translateX: [0, 3],
      translateY: [0, 2],
      boxShadow: [
        "0 0 0 transparent",
        "-2px -4px 0 var(--color-accent-dark), -4px -8px 0 color-mix(in oklch, var(--color-accent-dark) 40%, transparent)",
      ],
      duration: 280,
      easing: "cubicBezier(0.16,1,0.3,1)",
    });
    animate(fill, {
      translateX: ["-100%", "7%", "0%"],
      duration: 420,
      easing: "cubicBezier(0.16,1,0.3,1)",
    });
    animate(text, {
      color: "#141414",
      translateX: [0, -1, 0],
      duration: 420,
      easing: "cubicBezier(0.16,1,0.3,1)",
    });
  }

  function handleLeave() {
    const frame = frameRef.current;
    const fill = fillRef.current;
    const text = textRef.current;
    if (!frame || !fill || !text) return;
    utils.remove([frame, fill, text]);

    animate(frame, {
      translateX: 0,
      translateY: 0,
      boxShadow: "0 0 0 transparent",
      duration: 220,
      easing: "cubicBezier(0.7,0,0.84,0)",
    });
    animate(fill, {
      translateX: ["0%", "100%"],
      duration: 260,
      easing: "cubicBezier(0.7,0,0.84,0)",
    });
    animate(text, {
      color: "var(--accent)",
      translateX: 0,
      duration: 220,
      easing: "cubicBezier(0.7,0,0.84,0)",
    });
  }

  return (
    <a href="/" className="cursor-pointer" style={{ color: "var(--accent)" }}>
      <div className="grid gap-1">
        <div
          ref={frameRef}
          className="relative will-change-[transform,box-shadow]"
          onMouseEnter={handleEnter}
          onMouseLeave={handleLeave}
          onFocus={handleEnter}
          onBlur={handleLeave}
        >
          <div
            className="relative overflow-hidden font-mono font-bold text-4xl tracking-[0.2em] border border-x-4 px-2 text-center backdrop-blur-md"
            style={{
              borderColor: "var(--accent)",
              background: "color-mix(in oklch, var(--accent) 8%, transparent)",
            }}
          >
            <div ref={fillRef} className="absolute inset-0" style={{ background: "var(--accent)", transform: "translateX(-100%)" }} />
            <span ref={textRef} className="relative z-10" style={{ color: "var(--accent)" }}>ANIMA</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <div className="flex gap-[3px]">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="w-1.5 h-4" style={{ background: "var(--accent)" }} />
            ))}
          </div>
          <div className="px-1 py-px h-4 flex items-center" style={{ background: "var(--accent)" }}>
            <span className="font-mono text-[7px] uppercase font-semibold" style={{ color: "#141414" }}>
              ANIMA OPERATING SYSTEM
            </span>
          </div>
        </div>
      </div>
    </a>
  );
}
