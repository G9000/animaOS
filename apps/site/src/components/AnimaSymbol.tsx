import { useAnimaSymbol } from "@anima/standard-templates";

export default function AnimaSymbol() {
  const frame = useAnimaSymbol(0.6, "hello");

  return (
    <div className="relative">
      <pre
        className="font-mono text-[8px] sm:text-[9px] md:text-[10px] leading-[1.15] text-muted-foreground/40 select-none whitespace-pre"
        aria-hidden="true"
      >
        {frame.base}
      </pre>
      <pre
        className="absolute inset-0 font-mono text-[8px] sm:text-[9px] md:text-[10px] leading-[1.15] text-foreground/60 select-none whitespace-pre"
        aria-hidden="true"
      >
        {frame.text}
      </pre>
    </div>
  );
}
