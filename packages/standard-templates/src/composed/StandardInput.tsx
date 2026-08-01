import { forwardRef, useState } from "react";
import { cn } from "../utils/cn";
import { glass } from "../styles";
import { Button } from "../primitives/Button";
import { EyeIcon } from "../icons/EyeIcon";
import { EyeOffIcon } from "../icons/EyeOffIcon";

export interface StandardInputProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onBack?: () => void;
  password?: boolean;
  disabled?: boolean;
  loading?: boolean;
  error?: string;
  autoComplete?: string;
  spellCheck?: boolean;
  className?: string;
}

export const StandardInput = forwardRef<HTMLInputElement, StandardInputProps>(
  function StandardInput(
    {
      label,
      value,
      onChange,
      onSubmit,
      onBack,
      password = false,
      disabled = false,
      loading = false,
      error,
      autoComplete = "off",
      spellCheck = false,
      className,
    },
    ref,
  ) {
    const [showPassword, setShowPassword] = useState(false);

    return (
      <div
        className={cn(
          glass,
          className,
        )}
      >
        <div className="bg-accent px-2 py-1">
          <p
            key={label}
            className="font-mono text-ui font-semibold tracking-caps-4 text-foreground uppercase animate-fade-in"
          >
            {label}
          </p>
        </div>

        <div className="flex items-center border border-accent transition-all duration-300 w-82 h-12 focus-within:bg-accent/40 has-[input:not(:placeholder-shown)]:bg-accent/40 w-full">
          <input
            ref={ref}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onSubmit();
              if (e.key === "Escape") onBack?.();
            }}
            type={password && !showPassword ? "password" : "text"}
            placeholder=" "
            disabled={disabled}
            autoComplete={autoComplete}
            spellCheck={spellCheck}
            className="flex-1 p-4 bg-transparent font-mono text-body text-foreground outline-none text-left focus:text-accent-foreground [&:not(:placeholder-shown)]:text-accent-foreground transition-colors"
          />
          {password && (
            <button
              type="button"
              onClick={() => setShowPassword((v) => !v)}
              className="size-12 flex items-center justify-center text-accent shrink-0 cursor-pointer"
            >
              {showPassword ? <EyeOffIcon size="sm" /> : <EyeIcon size="sm" />}
            </button>
          )}
          <Button
            size="xs"
            variant="main"
            iconOnly
            icon={
              <svg
                width="13"
                height="13"
                viewBox="0 0 13 13"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M1 12L12 1M12 1H4.5M12 1v7.5" />
              </svg>
            }
            onClick={onSubmit}
            loading={loading}
            className="size-12"
          />
        </div>

        {error && (
          <p className="font-mono text-detail text-destructive animate-fade-in border-b border-x border-accent bg-destructive/10 uppercase px-2 py-1">
            [{error}]
          </p>
        )}
      </div>
    );
  },
);
