import { useAnimaSymbolSpinning } from "@anima/standard-templates";

export default function AnimaSymbolSpinning() {
  const frame = useAnimaSymbolSpinning();

  return (
    <div className="relative">
      {/* Glow layer — blurred duplicate behind */}
      <pre
        className="absolute inset-0 font-mono text-[14px] sm:text-[18px] md:text-[22px] leading-[1.2] text-primary/[0.07] select-none whitespace-pre blur-[6px]"
        aria-hidden="true"
      >
        {frame}
      </pre>
      {/* Main layer */}
      <pre
        className="relative font-mono text-[14px] sm:text-[18px] md:text-[22px] leading-[1.2] text-text-muted select-none whitespace-pre"
        aria-hidden="true"
      >
        {frame}
      </pre>
    </div>
  );
}
