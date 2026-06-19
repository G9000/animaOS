import { useRef, useState } from "react";
import { useTheme } from "../../hooks/useTheme";
import type { Theme } from "../../lib/theme";
import {
  useBackground,
  DEFAULT_BACKGROUND,
  type BackgroundConfig,
  type BackgroundType,
} from "../../hooks/useBackground";
import { inferBackgroundType } from "../../lib/background";

const THEME_OPTIONS: { value: Theme; label: string; hint: string }[] = [
  { value: "dark", label: "DARK", hint: "Low-light interface" },
  { value: "light", label: "LIGHT", hint: "Bright interface" },
  { value: "system", label: "SYSTEM", hint: "Match OS setting" },
];

const BACKGROUND_TYPES: { value: BackgroundType; label: string }[] = [
  { value: "default", label: "DEFAULT" },
  { value: "color", label: "COLOR" },
  { value: "gradient", label: "GRADIENT" },
  { value: "image", label: "IMAGE" },
  { value: "video", label: "VIDEO" },
];

const BACKGROUND_FITS: { value: "cover" | "contain" | "repeat"; label: string }[] = [
  { value: "cover", label: "COVER" },
  { value: "contain", label: "CONTAIN" },
  { value: "repeat", label: "REPEAT" },
];

function ThemeButton({
  option,
  active,
  onSelect,
}: {
  option: typeof THEME_OPTIONS[number];
  active: boolean;
  onSelect: (value: Theme) => void;
}) {
  return (
    <button
      onClick={() => onSelect(option.value)}
      className={[
        "flex-1 text-left p-3 border transition-all",
        active
          ? "bg-primary text-primary-foreground border-primary"
          : "bg-card text-muted-foreground border-border hover:text-foreground hover:bg-secondary",
      ].join(" ")}
    >
      <div className="font-mono text-[10px] tracking-[0.18em] uppercase">
        {option.label}
      </div>
      <div className="mt-1 font-mono text-[9px] tracking-wider text-current/60">
        {option.hint}
      </div>
    </button>
  );
}

function TypeButton({
  option,
  active,
  onSelect,
}: {
  option: typeof BACKGROUND_TYPES[number];
  active: boolean;
  onSelect: (value: BackgroundType) => void;
}) {
  return (
    <button
      onClick={() => onSelect(option.value)}
      className={[
        "flex-1 py-2 px-1 border font-mono text-[9px] tracking-[0.16em] uppercase transition-all",
        active
          ? "bg-primary text-primary-foreground border-primary"
          : "bg-card text-muted-foreground border-border hover:text-foreground hover:bg-secondary",
      ].join(" ")}
    >
      {option.label}
    </button>
  );
}

function FitButton({
  option,
  active,
  onSelect,
}: {
  option: typeof BACKGROUND_FITS[number];
  active: boolean;
  onSelect: (value: "cover" | "contain" | "repeat") => void;
}) {
  return (
    <button
      onClick={() => onSelect(option.value)}
      className={[
        "flex-1 py-1.5 px-2 border font-mono text-[9px] tracking-[0.14em] uppercase transition-all",
        active
          ? "bg-primary text-primary-foreground border-primary"
          : "bg-card text-muted-foreground border-border hover:text-foreground hover:bg-secondary",
      ].join(" ")}
    >
      {option.label}
    </button>
  );
}

function SliderField({
  label,
  value,
  min,
  max,
  step,
  onChange,
  format,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
  format: (value: number) => string;
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground">
          {label}
        </span>
        <span className="font-mono text-[9px] text-muted-foreground/60">
          {format(value)}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full h-1 bg-border appearance-none cursor-pointer accent-primary"
      />
    </div>
  );
}

export default function AppearanceSettings() {
  const { theme, set: setTheme } = useTheme();

  const {
    config: bgConfig,
    set: setBgConfig,
    saveFile: saveBgFile,
    url: bgUrl,
    loading: bgLoading,
  } = useBackground();
  const [bgUploading, setBgUploading] = useState(false);
  const [bgError, setBgError] = useState("");

  const bgInputRef = useRef<HTMLInputElement>(null);

  const handleBgTypeChange = (type: BackgroundType) => {
    const next: BackgroundConfig = { ...bgConfig, type };
    if (type === "default") {
      next.value = undefined;
    } else if (type === "image" || type === "video") {
      next.value = "";
      next.fit = next.fit ?? "cover";
    } else {
      next.value = bgConfig.value ?? getDefaultValueForType(type);
    }
    setBgConfig(next);
  };

  const getDefaultValueForType = (type: BackgroundType): string => {
    switch (type) {
      case "color": return "#1a1a18";
      case "gradient": return "linear-gradient(135deg, #1a1a18 0%, #353531 100%)";
      default: return "";
    }
  };

  const handleBgFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const type = inferBackgroundType(file.type);
    if (!type || (type !== "image" && type !== "video")) {
      setBgError("Unsupported file type. Use PNG, JPG, WEBP, GIF, MP4, or WEBM.");
      return;
    }

    setBgError("");
    setBgUploading(true);
    try {
      const value = await saveBgFile(file);
      setBgConfig({ ...bgConfig, type, value });
    } catch {
      setBgError("Failed to save background file.");
    } finally {
      setBgUploading(false);
    }
    if (bgInputRef.current) bgInputRef.current.value = "";
  };

  const renderBackgroundEditor = () => {
    switch (bgConfig.type) {
      case "default":
        return (
          <p className="font-mono text-[10px] text-muted-foreground/50 tracking-wider">
            Uses the current theme background.
          </p>
        );

      case "color":
        return (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <input
                type="color"
                value={bgConfig.value ?? "#000000"}
                onChange={(e) => setBgConfig({ ...bgConfig, value: e.target.value })}
                className="w-12 h-10 border border-border bg-transparent cursor-pointer"
              />
              <input
                type="text"
                value={bgConfig.value ?? ""}
                onChange={(e) => setBgConfig({ ...bgConfig, value: e.target.value })}
                placeholder="#1a1a18"
                className="flex-1 px-3 py-2 bg-input border border-border font-mono text-xs text-foreground placeholder:text-muted-foreground/30"
              />
            </div>
          </div>
        );

      case "gradient":
        return (
          <div className="space-y-3">
            <textarea
              value={bgConfig.value ?? ""}
              onChange={(e) => setBgConfig({ ...bgConfig, value: e.target.value })}
              placeholder="linear-gradient(135deg, #1a1a18 0%, #353531 100%)"
              rows={3}
              className="w-full px-3 py-2 bg-input border border-border font-mono text-xs text-foreground placeholder:text-muted-foreground/30 resize-none"
            />
            <p className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
              Enter any valid CSS background value.
            </p>
          </div>
        );

      case "image":
      case "video":
        return (
          <div className="space-y-3">
            <input
              ref={bgInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm"
              className="hidden"
              onChange={handleBgFileChange}
            />
            <div className="flex items-center gap-3">
              <button
                onClick={() => bgInputRef.current?.click()}
                disabled={bgUploading || bgLoading}
                className="font-mono text-[10px] tracking-wider px-4 py-2 border border-border text-muted-foreground hover:text-foreground hover:border-primary transition-colors disabled:opacity-40"
              >
                {bgUploading ? "SAVING..." : `UPLOAD ${bgConfig.type === "video" ? "VIDEO" : "IMAGE"}`}
              </button>
              <button
                onClick={() => setBgConfig({ ...DEFAULT_BACKGROUND })}
                className="font-mono text-[10px] tracking-wider px-4 py-2 border border-border text-muted-foreground/50 hover:text-destructive hover:border-destructive transition-colors"
              >
                RESET
              </button>
            </div>
            {bgConfig.value && (
              <div className="relative w-full h-40 overflow-hidden border border-border bg-card">
                {bgConfig.type === "video" && bgUrl ? (
                  <video
                    src={bgUrl}
                    autoPlay
                    muted
                    loop
                    playsInline
                    className="absolute inset-0 w-full h-full object-cover"
                  />
                ) : bgConfig.type === "image" && bgUrl ? (
                  <img
                    src={bgUrl}
                    alt="Background preview"
                    className="absolute inset-0 w-full h-full object-cover"
                  />
                ) : (
                  <div className="absolute inset-0 flex items-center justify-center">
                    <span className="font-mono text-[9px] tracking-wider text-muted-foreground/40 uppercase">
                      {bgLoading ? "Loading..." : "Preview unavailable"}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>
        );

      default:
        return null;
    }
  };

  return (
    <div className="space-y-8">
      <section className="space-y-4">
        <div className="space-y-1">
          <h2 className="font-mono text-[10px] tracking-wider text-foreground">
            THEME
          </h2>
          <p className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
            Choose the interface background and accent palette.
          </p>
        </div>

        <div className="flex gap-2">
          {THEME_OPTIONS.map((option) => (
            <ThemeButton
              key={option.value}
              option={option}
              active={theme === option.value}
              onSelect={setTheme}
            />
          ))}
        </div>
      </section>

      <section className="space-y-4">
        <div className="space-y-1">
          <h2 className="font-mono text-[10px] tracking-wider text-foreground">
            BACKGROUND
          </h2>
          <p className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
            Full-screen background behind the entire app. Media files are saved to disk.
          </p>
        </div>

        <div className="flex gap-1">
          {BACKGROUND_TYPES.map((option) => (
            <TypeButton
              key={option.value}
              option={option}
              active={bgConfig.type === option.value}
              onSelect={handleBgTypeChange}
            />
          ))}
        </div>

        {renderBackgroundEditor()}

        {bgConfig.type !== "default" && (
          <div className="space-y-4 pt-2 border-t border-border/40">
            {bgConfig.type === "image" && (
              <div className="space-y-2">
                <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground">
                  FIT
                </span>
                <div className="flex gap-1">
                  {BACKGROUND_FITS.map((option) => (
                    <FitButton
                      key={option.value}
                      option={option}
                      active={bgConfig.fit === option.value}
                      onSelect={(value) => setBgConfig({ ...bgConfig, fit: value })}
                    />
                  ))}
                </div>
              </div>
            )}

            <SliderField
              label="DIM"
              value={bgConfig.dim ?? 0}
              min={0}
              max={1}
              step={0.01}
              onChange={(value) => setBgConfig({ ...bgConfig, dim: value })}
              format={(v) => `${Math.round(v * 100)}%`}
            />

            <SliderField
              label="BLUR"
              value={bgConfig.blur ?? 0}
              min={0}
              max={32}
              step={0.5}
              onChange={(value) => setBgConfig({ ...bgConfig, blur: value })}
              format={(v) => `${v}px`}
            />
          </div>
        )}

        {bgError && (
          <p className="font-mono text-[10px] text-destructive tracking-wider">{bgError}</p>
        )}
      </section>
    </div>
  );
}
