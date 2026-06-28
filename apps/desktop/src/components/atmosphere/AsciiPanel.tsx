import { useEffect, useRef } from "react";
import { cn } from "@anima/standard-templates";
import { GSETS } from "@anima/ascii-motion";
import { useAsciiSettings } from "../../context/AsciiSettingsContext";

const GLYPH_SET_KEYS = Object.keys(GSETS);

const DENSITY = [
  { label: "LOW",  cols: 80  },
  { label: "MED",  cols: 120 },
  { label: "HIGH", cols: 160 },
] as const;

function SliderRow({ label, value, min, max, step, display, onChange }: {
  label: string; value: number; min: number; max: number;
  step: number; display: string; onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-2.5">
      <span className="font-mono text-[8px] tracking-[0.14em] text-muted-foreground uppercase w-[5.5rem] shrink-0">
        {label}
      </span>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={e => onChange(parseFloat(e.target.value))}
        className={cn(
          "flex-1 h-px cursor-pointer appearance-none bg-border",
          "[&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:size-2.5",
          "[&::-webkit-slider-thumb]:bg-accent [&::-webkit-slider-thumb]:cursor-pointer",
          "[&::-moz-range-thumb]:size-2.5 [&::-moz-range-thumb]:bg-accent",
          "[&::-moz-range-thumb]:border-0 [&::-moz-range-thumb]:cursor-pointer",
        )}
      />
      <span className="font-mono text-[9px] text-accent w-8 text-right shrink-0 tabular-nums">
        {display}
      </span>
    </div>
  );
}

function ToggleChip({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "font-mono text-[8px] tracking-[0.14em] uppercase px-2.5 py-1 border transition-colors duration-150",
        active
          ? "border-accent text-accent bg-accent/10"
          : "border-border text-muted-foreground hover:text-foreground hover:border-muted-foreground",
      )}
    >
      {label}
    </button>
  );
}

interface AsciiPanelProps {
  className?: string;
}

export function AsciiPanel({ className }: AsciiPanelProps) {
  const { settings, update, reset, srcOverride, srcOverrideName, setSrcOverride } = useAsciiSettings();

  useEffect(() => {
    if (!settings.randomizeGlyphs) return;
    const id = setInterval(() => {
      const next = GLYPH_SET_KEYS[Math.floor(Math.random() * GLYPH_SET_KEYS.length)];
      update({ glyphSet: next });
    }, 3000);
    return () => clearInterval(id);
  }, [settings.randomizeGlyphs, update]);

  const fileRef    = useRef<HTMLInputElement>(null);
  const blobUrlRef = useRef<string | undefined>(undefined);

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    const url = URL.createObjectURL(file);
    blobUrlRef.current = url;
    setSrcOverride(url, file.type.startsWith("image/") ? "image" : "video", file.name);
    e.target.value = "";
  }

  function clearUpload() {
    if (blobUrlRef.current) { URL.revokeObjectURL(blobUrlRef.current); blobUrlRef.current = undefined; }
    setSrcOverride(undefined, undefined, undefined);
  }

  useEffect(() => () => { if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current); }, []);

  return (
    <div className={cn("w-72 p-4 flex flex-col gap-3", className)}>

      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[8px] tracking-[0.14em] text-muted-foreground uppercase shrink-0">
          Glyph Set
        </span>
        <div className="flex items-center gap-1.5">
          <select
            value={settings.glyphSet}
            onChange={e => update({ glyphSet: e.target.value })}
            disabled={settings.randomizeGlyphs}
            className={cn(
              "font-mono text-[9px] tracking-[0.1em] uppercase cursor-pointer",
              "bg-transparent border border-border text-accent px-2 py-1",
              "focus:outline-none focus:border-accent",
              "disabled:opacity-40 disabled:cursor-not-allowed",
            )}
          >
            {GLYPH_SET_KEYS.map(k => (
              <option key={k} value={k} className="bg-background text-foreground normal-case">{k}</option>
            ))}
          </select>
          <ToggleChip
            label="↺ rand"
            active={settings.randomizeGlyphs}
            onClick={() => update({ randomizeGlyphs: !settings.randomizeGlyphs })}
          />
        </div>
      </div>

      <SliderRow label="Contrast"   value={settings.contrast}   min={0.5} max={3.0} step={0.05}
        display={settings.contrast.toFixed(2)} onChange={v => update({ contrast: v })} />

      <SliderRow label="Brightness" value={settings.brightness} min={-50} max={20}  step={1}
        display={String(settings.brightness)}  onChange={v => update({ brightness: v })} />

      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[8px] tracking-[0.14em] text-muted-foreground uppercase shrink-0">Density</span>
        <div className="flex gap-1">
          {DENSITY.map(p => (
            <button
              key={p.label}
              onClick={() => update({ cols: p.cols })}
              className={cn(
                "font-mono text-[8px] tracking-[0.14em] uppercase px-2.5 py-1 border transition-colors duration-150",
                settings.cols === p.cols
                  ? "border-accent text-accent bg-accent/10"
                  : "border-border text-muted-foreground hover:text-foreground hover:border-muted-foreground",
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex gap-2">
        <ToggleChip label="Color"       active={settings.color}       onClick={() => update({ color: !settings.color })} />
        <ToggleChip label="Edge detect" active={settings.edgeDetect}  onClick={() => update({ edgeDetect: !settings.edgeDetect })} />
      </div>

      <div className="h-px bg-border" />

      <div className="flex flex-col gap-1.5">
        <span className="font-mono text-[8px] tracking-[0.14em] text-muted-foreground uppercase">Background</span>
        {srcOverride ? (
          <div className="flex items-center gap-2">
            <span className="font-mono text-[8px] text-accent truncate flex-1 min-w-0" title={srcOverrideName}>
              {srcOverrideName ?? "custom file"}
            </span>
            <button onClick={clearUpload} className="font-mono text-[8px] text-muted-foreground hover:text-destructive transition-colors shrink-0">
              ✕ reset
            </button>
          </div>
        ) : (
          <button
            onClick={() => fileRef.current?.click()}
            className="font-mono text-[8px] tracking-[0.14em] uppercase px-3 py-1.5 border border-border text-muted-foreground hover:text-foreground hover:border-muted-foreground transition-colors text-left"
          >
            ↑ upload image or video
          </button>
        )}
        <input ref={fileRef} type="file" accept="image/*,video/*" className="hidden" onChange={handleFile} />
      </div>

      <button
        onClick={() => { clearUpload(); reset(); }}
        className="font-mono text-[8px] tracking-[0.14em] uppercase text-muted-foreground/70 hover:text-muted-foreground transition-colors text-left pt-1"
      >
        ↺ reset to defaults
      </button>
    </div>
  );
}
