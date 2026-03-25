import { type ComponentProps, type ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../utils/cn";

export const buttonVariants = cva(
  // Base — shared across all variants
  [
    "group relative overflow-hidden font-mono tracking-widest uppercase cursor-pointer",
    "inline-flex items-center justify-center gap-1.5",
    "transition-[border-color,opacity] duration-300",
    "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
    "disabled:opacity-40 disabled:cursor-not-allowed disabled:before:hidden",
    // Fill layer
    "before:absolute before:inset-0 before:-translate-x-full",
    "before:transition-transform before:duration-500 before:ease-[cubic-bezier(0.16,1,0.3,1)]",
    "hover:before:translate-x-0",
  ],
  {
    variants: {
      variant: {
        fill:   "border border-border/50 dark:border-border hover:border-ring before:bg-foreground",
        invert: "border border-foreground/20 dark:border-foreground/30 hover:border-foreground before:bg-foreground",
        ghost:  "before:bg-foreground/8",
        danger: "border border-border/50 dark:border-border hover:border-destructive before:bg-destructive",
      },
      size: {
        xs: "text-label px-2.5 py-1 gap-1",
        sm: "text-caption px-4 py-1.5",
        md: "text-body px-5 py-2",
        lg: "text-ui px-6 py-2.5",
      },
      iconOnly: {
        true:  "px-0",
        false: "",
      },
    },
    compoundVariants: [
      { size: "xs", iconOnly: true, class: "w-6 h-6" },
      { size: "sm", iconOnly: true, class: "w-7 h-7" },
      { size: "md", iconOnly: true, class: "w-8 h-8" },
      { size: "lg", iconOnly: true, class: "w-10 h-10" },
    ],
    defaultVariants: {
      variant: "fill",
      size: "sm",
      iconOnly: false,
    },
  },
);

const textVariants = cva(
  "relative z-10 inline-flex items-center gap-[inherit] transition-colors duration-300 tracking-widest",
  {
    variants: {
      variant: {
        fill:   "text-muted-foreground group-hover:text-background",
        invert: "text-foreground group-hover:text-background",
        ghost:  "text-muted-foreground group-hover:text-foreground",
        danger: "text-muted-foreground group-hover:text-destructive-foreground",
      },
    },
    defaultVariants: { variant: "fill" },
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

export function Button({
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
}: ButtonProps) {
  return (
    <button
      disabled={disabled || loading}
      className={cn(buttonVariants({ variant, size, iconOnly }), className)}
      {...props}
    >
      <span className={textVariants({ variant })}>
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
}

function LoadingDots() {
  return (
    <span className="inline-flex gap-0.5 items-center">
      <span className="w-[3px] h-[3px] rounded-full bg-current animate-pulse" />
      <span className="w-[3px] h-[3px] rounded-full bg-current animate-pulse [animation-delay:150ms]" />
      <span className="w-[3px] h-[3px] rounded-full bg-current animate-pulse [animation-delay:300ms]" />
    </span>
  );
}
