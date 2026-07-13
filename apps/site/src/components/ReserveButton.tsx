import { useRef } from "react";
import { animate, utils } from "animejs";

export default function ReserveButton() {
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
    <div
      ref={frameRef}
      className="relative inline-block will-change-[transform,box-shadow]"
      onMouseEnter={handleEnter}
      onMouseLeave={handleLeave}
      onFocus={handleEnter}
      onBlur={handleLeave}
    >
      <a
        href="#waitlist"
        className="relative overflow-hidden font-mono font-bold text-lg tracking-[0.3em] border px-8 py-3 backdrop-blur-md block"
        style={{
          borderColor: "var(--accent)",
          borderLeftWidth: 4,
          borderRightWidth: 4,
          color: "var(--accent)",
          background: "color-mix(in oklch, var(--accent) 8%, transparent)",
        }}
      >
        <div ref={fillRef} className="absolute inset-0" style={{ background: "var(--accent)", transform: "translateX(-100%)" }} />
        <span ref={textRef} className="relative z-10" style={{ color: "var(--accent)" }}>RESERVE ACCESS</span>
      </a>
    </div>
  );
}
