import { useRef, useState } from "react";
import { useTheme } from "../../hooks/useTheme";
import type { Theme } from "../../lib/theme";
import {
  useBackground,
  DEFAULT_BACKGROUND,
  type BackgroundConfig,
  type BackgroundType,
} from "../../hooks/useBackground";
import { useClockFormat } from "../../hooks/useClockFormat";
import { inferBackgroundType } from "../../lib/background";
import { cn } from "@anima/standard-templates";

const glass = "bg-background/25 backdrop-blur-[40px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.22)]";
const INPUT = "w-full bg-foreground/[0.04] border border-foreground/[0.08] px-3 py-2.5 font-mono text-[11px] text-foreground placeholder:text-foreground/25 outline-none focus:border-foreground/[0.18] transition-colors";

const THEME_OPTIONS: { value: Theme; label: string; hint: string; bg: string; fg: string; accent: string }[] = [
  { value: "dark",   label: "Dark",   hint: "Low-light",   bg: "#191917", fg: "#e8e3d5", accent: "#c2622a" },
  { value: "light",  label: "Light",  hint: "Bright",      bg: "#ede8d8", fg: "#2e2b26", accent: "#c2622a" },
  { value: "system", label: "System", hint: "Match OS",    bg: "#191917", fg: "#e8e3d5", accent: "#c2622a" },
];

const BACKGROUND_TYPES: { value: BackgroundType; label: string }[] = [
  { value: "default",  label: "Default" },
  { value: "color",    label: "Color" },
  { value: "gradient", label: "Gradient" },
  { value: "image",    label: "Image" },
  { value: "video",    label: "Video" },
];

const BACKGROUND_FITS: { value: "cover" | "contain" | "repeat"; label: string }[] = [
  { value: "cover",   label: "Cover" },
  { value: "contain", label: "Contain" },
  { value: "repeat",  label: "Repeat" },
];

function ThemeCard({ label, hint, bg, fg, accent, active, isSystem, onClick }: {
  value: Theme; label: string; hint: string; bg: string; fg: string; accent: string;
  active: boolean; isSystem?: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "flex-1 flex flex-col gap-3 p-3 border transition-all duration-150",
        active
          ? "border-accent/60 bg-accent/[0.06]"
          : "border-foreground/[0.07] hover:border-foreground/[0.15] hover:bg-foreground/[0.03]",
      )}
    >
      {/* Mini UI preview */}
      <div className="w-full h-14 overflow-hidden border border-foreground/[0.06] relative" style={isSystem ? undefined : { background: bg }}>
        {isSystem ? (
          <>
            <div className="absolute inset-0 left-0 w-1/2" style={{ background: "#191917" }} />
            <div className="absolute inset-0 right-0 left-1/2" style={{ background: "#ede8d8" }} />
            <div className="absolute inset-x-0 top-0 bottom-0 flex items-center justify-center">
              <div className="w-px h-full bg-foreground/10" />
            </div>
          </>
        ) : null}
        {/* mini sidebar */}
        <div className="absolute left-0 top-0 bottom-0 w-5 border-r" style={{ background: isSystem ? "rgba(0,0,0,0.25)" : `${bg}cc`, borderColor: `${fg}12` }} />
        {/* mini content lines */}
        <div className="absolute left-7 top-3 right-2 space-y-1.5">
          <div className="h-1 w-3/4 rounded-sm opacity-30" style={{ background: fg }} />
          <div className="h-1 w-1/2 rounded-sm opacity-15" style={{ background: fg }} />
        </div>
        {/* accent dot */}
        <div className="absolute bottom-2.5 left-7 h-1.5 w-6 rounded-sm" style={{ background: accent }} />
      </div>

      <div className="text-left space-y-0.5">
        <div className={cn("font-mono text-[10px] tracking-[0.1em] transition-colors", active ? "text-accent" : "text-foreground/60")}>
          {label}
        </div>
        <div className="font-mono text-[8px] text-foreground/25 tracking-wide">{hint}</div>
      </div>

      {active && <div className="w-full h-px bg-accent/40" />}
    </button>
  );
}

function Slider({ label, value, min, max, step, onChange, format }: {
  label: string; value: number; min: number; max: number; step: number;
  onChange: (v: number) => void; format: (v: number) => string;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between">
        <span className="font-mono text-[8px] tracking-[0.22em] uppercase text-foreground/30">{label}</span>
        <span className="font-mono text-[9px] text-foreground/35 tabular-nums">{format(value)}</span>
      </div>
      <div className="relative h-4 flex items-center">
        <div className="absolute inset-x-0 h-px bg-foreground/[0.10]" />
        <div className="absolute left-0 h-px bg-accent/50 transition-all" style={{ width: `${pct}%` }} />
        <input
          type="range" min={min} max={max} step={step} value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          className="relative w-full h-px appearance-none bg-transparent cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-accent [&::-webkit-slider-thumb]:border [&::-webkit-slider-thumb]:border-accent-dark"
        />
      </div>
    </div>
  );
}

export default function AppearanceSettings() {
  const { theme, effective, set: setTheme } = useTheme();
  const { config: bgConfig, set: setBgConfig, saveFile: saveBgFile, url: bgUrl, loading: bgLoading } = useBackground();
  const { format: clockFormat, setFormat: setClockFormat } = useClockFormat();
  const [bgUploading, setBgUploading] = useState(false);
  const [bgError, setBgError] = useState("");
  const bgInputRef = useRef<HTMLInputElement>(null);

  const handleBgTypeChange = (type: BackgroundType) => {
    const next: BackgroundConfig = { ...bgConfig, type };
    if (type === "default") { next.value = undefined; }
    else if (type === "image" || type === "video") { next.value = ""; next.fit = next.fit ?? "cover"; }
    else { next.value = bgConfig.value ?? (type === "color" ? "#1a1a18" : "linear-gradient(135deg, #1a1a18 0%, #353531 100%)"); }
    setBgConfig(next);
  };

  const handleBgFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const type = inferBackgroundType(file.type);
    if (!type || (type !== "image" && type !== "video")) { setBgError("Unsupported file type."); return; }
    setBgError(""); setBgUploading(true);
    try { setBgConfig({ ...bgConfig, type, value: await saveBgFile(file) }); }
    catch { setBgError("Failed to save background file."); }
    finally { setBgUploading(false); if (bgInputRef.current) bgInputRef.current.value = ""; }
  };

  const renderBgEditor = () => {
    switch (bgConfig.type) {
      case "default":
        return (
          <p className="font-mono text-[9px] text-foreground/25 tracking-wide leading-relaxed">
            Uses the current theme's default background.
          </p>
        );

      case "color":
        return (
          <div className="flex items-center gap-3">
            <div className="relative">
              <input
                type="color"
                value={bgConfig.value ?? "#000000"}
                onChange={(e) => setBgConfig({ ...bgConfig, value: e.target.value })}
                className="absolute inset-0 opacity-0 w-full h-full cursor-pointer"
              />
              <div className="w-10 h-10 border border-foreground/[0.1]" style={{ background: bgConfig.value ?? "#000" }} />
            </div>
            <input
              type="text"
              value={bgConfig.value ?? ""}
              onChange={(e) => setBgConfig({ ...bgConfig, value: e.target.value })}
              placeholder="#1a1a18"
              className={INPUT}
            />
          </div>
        );

      case "gradient":
        return (
          <div className="space-y-3">
            {bgConfig.value && (
              <div className="h-8 w-full border border-foreground/[0.08]" style={{ background: bgConfig.value }} />
            )}
            <textarea
              value={bgConfig.value ?? ""}
              onChange={(e) => setBgConfig({ ...bgConfig, value: e.target.value })}
              placeholder="linear-gradient(135deg, #1a1a18 0%, #353531 100%)"
              rows={2}
              className={`${INPUT} resize-none`}
            />
            <p className="font-mono text-[8px] text-foreground/20 tracking-wide">Any valid CSS background value.</p>
          </div>
        );

      case "image":
      case "video": {
        const hasFile = Boolean(bgConfig.value);
        return (
          <div className="space-y-3">
            <input
              ref={bgInputRef} type="file"
              accept="image/png,image/jpeg,image/webp,image/gif,video/mp4,video/webm"
              className="hidden"
              onChange={handleBgFileChange}
            />
            {hasFile && bgUrl ? (
              <div className="relative w-full h-40 border border-foreground/[0.08] overflow-hidden bg-foreground/[0.02]">
                {bgConfig.type === "video"
                  ? <video src={bgUrl} autoPlay muted loop playsInline className="absolute inset-0 w-full h-full object-cover" />
                  : <img src={bgUrl} alt="Background" className="absolute inset-0 w-full h-full object-cover" />}
                <div className="absolute inset-0 bg-gradient-to-t from-black/40 to-transparent" />
                <div className="absolute bottom-2 right-2 flex gap-1.5">
                  <button
                    onClick={() => bgInputRef.current?.click()}
                    disabled={bgUploading}
                    className="font-mono text-[8px] tracking-[0.14em] uppercase px-2.5 py-1.5 bg-background/70 backdrop-blur-sm border border-foreground/[0.12] text-foreground/60 hover:text-foreground/90 disabled:opacity-30 transition-colors"
                  >
                    Replace
                  </button>
                  <button
                    onClick={() => setBgConfig({ ...DEFAULT_BACKGROUND })}
                    className="font-mono text-[8px] tracking-[0.14em] uppercase px-2.5 py-1.5 bg-background/70 backdrop-blur-sm border border-destructive/30 text-destructive/60 hover:text-destructive/90 transition-colors"
                  >
                    Remove
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => bgInputRef.current?.click()}
                disabled={bgUploading || bgLoading}
                className="w-full py-8 border border-dashed border-foreground/[0.12] hover:border-foreground/[0.22] hover:bg-foreground/[0.02] transition-all group"
              >
                <div className="flex flex-col items-center gap-2">
                  <svg className="text-foreground/20 group-hover:text-foreground/40 transition-colors" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" />
                  </svg>
                  <span className="font-mono text-[8px] tracking-[0.18em] uppercase text-foreground/25 group-hover:text-foreground/50 transition-colors">
                    {bgUploading ? "Saving..." : `Upload ${bgConfig.type === "video" ? "video" : "image"}`}
                  </span>
                </div>
              </button>
            )}
          </div>
        );
      }

      default: return null;
    }
  };

  return (
    <div className="space-y-3">

      {/* ── Theme ── */}
      <div className={glass}>
        <div className="px-5 pt-4 pb-3 border-b border-foreground/[0.08] flex items-center justify-between">
          <h2 className="font-mono text-[9px] tracking-[0.26em] uppercase text-foreground/40">Theme</h2>
          <span className="font-mono text-[8px] tracking-[0.14em] uppercase text-foreground/20">
            {effective === "dark" ? "Dark mode active" : "Light mode active"}
          </span>
        </div>
        <div className="p-4 flex gap-2">
          {THEME_OPTIONS.map((opt) => (
            <ThemeCard
              key={opt.value}
              {...opt}
              active={theme === opt.value}
              isSystem={opt.value === "system"}
              onClick={() => setTheme(opt.value)}
            />
          ))}
        </div>
      </div>

      {/* ── Background ── */}
      <div className={glass}>
        <div className="px-5 pt-4 pb-3 border-b border-foreground/[0.08]">
          <h2 className="font-mono text-[9px] tracking-[0.26em] uppercase text-foreground/40">Background</h2>
        </div>

        {/* Type tabs */}
        <div className="flex border-b border-foreground/[0.05]">
          {BACKGROUND_TYPES.map((opt) => (
            <button
              key={opt.value}
              onClick={() => handleBgTypeChange(opt.value)}
              className={cn(
                "flex-1 py-2.5 font-mono text-[8px] tracking-[0.16em] uppercase transition-all border-b-[2px] -mb-px",
                bgConfig.type === opt.value
                  ? "border-accent text-foreground/75"
                  : "border-transparent text-foreground/25 hover:text-foreground/50",
              )}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <div className="p-4 space-y-4">
          {renderBgEditor()}

          {bgConfig.type !== "default" && (
            <div className="space-y-4 pt-3 border-t border-foreground/[0.06]">
              {bgConfig.type === "image" && (
                <div className="space-y-2">
                  <span className="font-mono text-[8px] tracking-[0.22em] uppercase text-foreground/30">Fit</span>
                  <div className="flex gap-1">
                    {BACKGROUND_FITS.map((opt) => (
                      <button
                        key={opt.value}
                        onClick={() => setBgConfig({ ...bgConfig, fit: opt.value })}
                        className={cn(
                          "flex-1 py-2 font-mono text-[8px] tracking-[0.14em] uppercase border transition-all",
                          bgConfig.fit === opt.value
                            ? "border-foreground/[0.18] bg-foreground/[0.08] text-foreground/80"
                            : "border-foreground/[0.07] text-foreground/30 hover:text-foreground/60 hover:border-foreground/[0.14]",
                        )}
                      >
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <Slider label="Dim" value={bgConfig.dim ?? 0} min={0} max={1} step={0.01} onChange={(v) => setBgConfig({ ...bgConfig, dim: v })} format={(v) => `${Math.round(v * 100)}%`} />
              <Slider label="Blur" value={bgConfig.blur ?? 0} min={0} max={32} step={0.5} onChange={(v) => setBgConfig({ ...bgConfig, blur: v })} format={(v) => `${v}px`} />
            </div>
          )}

          {bgError && <p className="font-mono text-[9px] text-destructive/70 tracking-wide">{bgError}</p>}
        </div>
      </div>

      {/* ── Clock ── */}
      <div className={glass}>
        <div className="px-5 pt-4 pb-3 border-b border-foreground/[0.08] flex items-center justify-between">
          <h2 className="font-mono text-[9px] tracking-[0.26em] uppercase text-foreground/40">Clock</h2>
          <span className="font-mono text-[8px] tracking-[0.14em] uppercase text-foreground/20">
            Nav bar display
          </span>
        </div>
        <div className="p-4 flex gap-2">
          {([
            { value: "24h" as const, label: "24H", preview: "13:45" },
            { value: "12h" as const, label: "12H", preview: "01:45 PM" },
          ]).map(({ value, label, preview }) => (
            <button
              key={value}
              onClick={() => setClockFormat(value)}
              className={cn(
                "flex-1 flex flex-col gap-2.5 p-3 border transition-all duration-150",
                clockFormat === value
                  ? "border-accent/60 bg-accent/[0.06]"
                  : "border-foreground/[0.07] hover:border-foreground/[0.15] hover:bg-foreground/[0.03]",
              )}
            >
              <span className="font-mono text-[18px] tracking-[0.12em] text-foreground/50 leading-none">
                {preview}
              </span>
              <div className="flex items-center justify-between">
                <span className={cn(
                  "font-mono text-[10px] tracking-[0.1em] transition-colors",
                  clockFormat === value ? "text-accent" : "text-foreground/50",
                )}>
                  {label}
                </span>
              </div>
              {clockFormat === value && <div className="w-full h-px bg-accent/40" />}
            </button>
          ))}
        </div>
      </div>

    </div>
  );
}
