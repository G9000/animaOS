import { forwardRef, type ComponentProps, type ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../utils/cn";

export const buttonVariants = cva(
  // Base — shared across all variants
  [
    "group relative overflow-hidden font-mono tracking-[0.22em] uppercase cursor-pointer",
    "inline-flex items-center justify-center gap-1.5",
    "border rounded-none transition-all duration-200",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
    "disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none disabled:translate-y-0",
    "active:translate-y-[1px]",
  ],
  {
    variants: {
      variant: {
        fill:
          "border-primary bg-primary text-primary-foreground hover:hover:bg-primary/90 active:",
        invert:
          "border-border bg-card text-foreground hover:hover:bg-secondary active:",
        ghost:
          "border-transparent bg-transparent text-muted-foreground hover:text-foreground hover:bg-secondary/60 active:bg-secondary",
        danger:
          "border-destructive bg-destructive text-destructive-foreground hover:hover:bg-destructive/90 active:",
        accent:
          "border-accent bg-accent text-accent-foreground hover:hover:bg-accent/90 active:",
      },
      size: {
        xs: "text-[9px] px-3 py-1.5 gap-1",
        sm: "text-[10px] px-4 py-2",
        md: "text-[11px] px-5 py-2.5",
        lg: "text-xs px-6 py-3",
      },
      iconOnly: {
        true:  "px-0",
        false: "",
      },
    },
    compoundVariants: [
      { size: "xs", iconOnly: true, class: "w-7 h-7" },
      { size: "sm", iconOnly: true, class: "w-8 h-8" },
      { size: "md", iconOnly: true, class: "w-9 h-9" },
      { size: "lg", iconOnly: true, class: "w-11 h-11" },
    ],
    defaultVariants: {
      variant: "fill",
      size: "sm",
      iconOnly: false,
    },
  },
);

export type ButtonVariant = NonNullable<VariantProps<typeof buttonVariants>["variant"]>;
export type ButtonSize    = NonNullable<VariantProps<typeof buttonVariants>["size"]>;

export interface ButtonProps
  extends Omit<ComponentProps<"button">, "size">,
    VariantProps<typeof buttonVariants> {
  loading?: boolean;
  icon?: ReactNode;
  iconPosition?: "left" | "right";
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({
    variant = "fill",
    size = "sm",
    iconOnly = false,
    loading = false,
    disabled,
    icon,
    iconPosition = "left",
    className,
    children,
    ...props
  }, ref) => {
    return (
      <button
        ref={ref}
        disabled={disabled || loading}
        className={cn(buttonVariants({ variant, size, iconOnly }), className)}
        {...props}
      >
        <span className="relative z-10 inline-flex items-center gap-[inherit]">
          {loading ? (
            <LoadingDots />
          ) : (
            <>
              {icon && iconPosition === "left"  && <span className="shrink-0">{icon}</span>}
              {!iconOnly && children}
              {icon && iconPosition === "right" && <span className="shrink-0">{icon}</span>}
            </>
          )}
        </span>
      </button>
    );
  },
);

Button.displayName = "Button";

function LoadingDots() {
  return (
    <span className="inline-flex gap-1 items-center">
      <span className="w-1 h-1 rounded-none bg-current animate-pulse" />
      <span className="w-1 h-1 rounded-none bg-current animate-pulse [animation-delay:150ms]" />
      <span className="w-1 h-1 rounded-none bg-current animate-pulse [animation-delay:300ms]" />
    </span>
  );
}
