import { cn } from "../utils/cn";

export interface IconProps {
  className?: string;
  size?: "sm" | "md" | "lg" | "xl";
  strokeWidth?: number;
  glow?: boolean;
  glitch?: boolean;
}

const sizeMap = {
  sm: "size-4",
  md: "size-5",
  lg: "size-6",
  xl: "size-8",
};

interface BaseIconProps extends IconProps {
  children: React.ReactNode;
}

export function BaseIcon({
  className,
  size = "sm",
  strokeWidth = 1.5,
  glow = false,
  glitch = false,
  children,
}: BaseIconProps) {
  return (
    <span
      className={cn("relative inline-flex shrink-0", glitch && "icon-glitch")}
    >
      {/* Glow layer */}
      {glow && (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          strokeLinecap="square"
          strokeLinejoin="miter"
          className={cn(
            "absolute inset-0 blur-[2px] opacity-50",
            sizeMap[size],
          )}
          style={{ color: "var(--color-primary, currentColor)" }}
        >
          {children}
        </svg>
      )}

      {/* Main icon - sharp corners for tech feel */}
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="square"
        strokeLinejoin="miter"
        className={cn(sizeMap[size], className, "relative z-10")}
      >
        {children}
      </svg>

      {/* Glitch offset layer */}
      {glitch && (
        <svg
          aria-hidden="true"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          strokeLinecap="square"
          strokeLinejoin="miter"
          className={cn(
            "absolute inset-0 opacity-0",
            sizeMap[size],
            "glitch-layer",
          )}
          style={{
            color: "var(--color-danger, #d04848)",
            transform: "translate(1px, 0)",
          }}
        >
          {children}
        </svg>
      )}
    </span>
  );
}
