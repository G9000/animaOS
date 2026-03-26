import { useAnimaSymbolSpinning } from "@anima/standard-templates";

export default function AnimaSymbolSpinning() {
  const frame = useAnimaSymbolSpinning();

  return (
    <div className="relative">
      {/* Glow layer — blurred duplicate behind */}
      <pre
        className="absolute inset-0 font-mono text-[11px] sm:text-[14px] md:text-[16px] leading-[1.2] text-primary/[0.07] select-none whitespace-pre blur-[6px]"
        aria-hidden="true"
      >
        {frame}
      </pre>
      {/* Main layer */}
      <pre
        className="relative font-mono text-[11px] sm:text-[14px] md:text-[16px] leading-[1.2] text-muted-foreground select-none whitespace-pre"
        aria-hidden="true"
      >
        {frame}
      </pre>
    </div>
  );
}
