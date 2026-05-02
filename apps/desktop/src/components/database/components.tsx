import type { ReactNode } from "react";

export function StatCard({
  title,
  value,
  icon,
  color,
  subtitle,
}: {
  title: string;
  value: string | number;
  icon: ReactNode;
  color: string;
  subtitle?: string;
}) {
  return (
    <div
      className={`bg-card border ${color} rounded-none p-4 hover:border-primary/30 transition-colors`}
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-[11px] text-muted-foreground uppercase tracking-wide">
            {title}
          </p>
          <p className="text-2xl font-semibold mt-1">{value}</p>
          {subtitle && (
            <p className="text-[11px] text-muted-foreground/60 mt-0.5">{subtitle}</p>
          )}
        </div>
        <div className="text-muted-foreground/40">{icon}</div>
      </div>
    </div>
  );
}

export function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-2.5 py-1 text-[11px] rounded border transition-colors ${
        active
          ? "bg-card border-border text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground"
      }`}
    >
      {children}
    </button>
  );
}

export function NavButton({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-none transition-all ${
        active
          ? "bg-primary/15 text-primary border border-primary/20"
          : "text-muted-foreground hover:text-foreground hover:bg-input border border-transparent"
      }`}
    >
      {icon}
      {children}
    </button>
  );
}

export function ViewModeButton<T extends string>({
  mode,
  current,
  onClick,
  icon,
}: {
  mode: T;
  current: T;
  onClick: (m: T) => void;
  icon: ReactNode;
}) {
  return (
    <button
      onClick={() => onClick(mode)}
      className={`p-1.5 rounded transition-colors ${
        current === mode
          ? "bg-primary/20 text-primary"
          : "text-muted-foreground hover:text-foreground hover:bg-input"
      }`}
      title={`${mode} view`}
    >
      {icon}
    </button>
  );
}

export function Button({
  onClick,
  disabled,
  variant = "default",
  size = "md",
  children,
  className = "",
}: {
  onClick?: () => void;
  disabled?: boolean;
  variant?: "default" | "primary" | "danger" | "ghost";
  size?: "sm" | "md";
  children: ReactNode;
  className?: string;
}) {
  const base =
    "rounded-none transition-colors disabled:opacity-30 flex items-center gap-1.5";

  const variants = {
    default: "bg-card border border-border hover:bg-input text-foreground",
    primary: "bg-primary text-white hover:bg-primary/90 border border-transparent",
    danger: "bg-destructive/20 text-destructive hover:bg-destructive/30 border border-transparent",
    ghost: "bg-transparent hover:bg-input text-muted-foreground hover:text-foreground border border-transparent",
  };

  const sizes = {
    sm: "px-2 py-1 text-[10px]",
    md: "px-3 py-1.5 text-xs",
  };

  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`${base} ${variants[variant]} ${sizes[size]} ${className}`}
    >
      {children}
    </button>
  );
}
