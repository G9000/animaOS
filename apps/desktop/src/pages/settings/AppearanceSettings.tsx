import { useRef, useState } from "react";
import {
  getCustomBanner,
  saveCustomBanner,
  clearCustomBanner,
  BANNER_MAX_BYTES,
  type BannerConfig,
} from "../../lib/preferences";
import banner1 from "../../assets/banner_1.jpg";
import banner2 from "../../assets/banner_2.jpg";

const DEFAULT_BANNER = banner1;
const _BUILTIN_BANNERS = [banner1, banner2];

export default function AppearanceSettings() {
  const [config, setConfig] = useState<BannerConfig | null>(() => getCustomBanner());
  const [uploading, setUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState("");

  const fileInputRef = useRef<HTMLInputElement>(null);
  const previewRef = useRef<HTMLDivElement>(null);
  const dragOriginRef = useRef<{ mouseX: number; mouseY: number; posX: number; posY: number } | null>(null);

  const activeBannerUrl = config?.url ?? DEFAULT_BANNER;
  const posX = config?.x ?? 50;
  const posY = config?.y ?? 50;

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > BANNER_MAX_BYTES) { setError("Image must be under 5 MB."); return; }
    setError("");
    setUploading(true);
    const reader = new FileReader();
    reader.onload = (ev) => {
      const dataUrl = ev.target?.result as string;
      const next: BannerConfig = { url: dataUrl, x: 50, y: 50 };
      setConfig(next);
      saveCustomBanner(next);
      setUploading(false);
    };
    reader.onerror = () => { setError("Failed to read file."); setUploading(false); };
    reader.readAsDataURL(file);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const handlePreviewMouseDown = (e: React.MouseEvent) => {
    if (!config) return;
    e.preventDefault();
    dragOriginRef.current = { mouseX: e.clientX, mouseY: e.clientY, posX, posY };
    setIsDragging(true);

    const onMouseMove = (me: MouseEvent) => {
      const origin = dragOriginRef.current;
      const container = previewRef.current;
      if (!origin || !container) return;
      const { width, height } = container.getBoundingClientRect();
      const dx = me.clientX - origin.mouseX;
      const dy = me.clientY - origin.mouseY;
      const newX = Math.max(0, Math.min(100, origin.posX - (dx / width * 100)));
      const newY = Math.max(0, Math.min(100, origin.posY - (dy / height * 100)));
      setConfig((prev) => prev ? { ...prev, x: newX, y: newY } : null);
    };

    const onMouseUp = () => {
      dragOriginRef.current = null;
      setIsDragging(false);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
      setConfig((prev) => { if (prev) saveCustomBanner(prev); return prev; });
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
  };

  const handleRemove = () => {
    setConfig(null);
    clearCustomBanner();
    setError("");
  };

  return (
    <div className="space-y-8">
      <section className="space-y-4">
        <div className="space-y-1">
          <h2 className="font-mono text-[10px] tracking-wider text-foreground">
            DASHBOARD BANNER
          </h2>
          <p className="font-mono text-[9px] text-muted-foreground/40 tracking-wider">
            PNG, JPG, WEBP or GIF — max 5 MB. Stored locally.
          </p>
        </div>

        {/* Preview / reposition area */}
        <div
          ref={previewRef}
          onMouseDown={handlePreviewMouseDown}
          className={[
            "relative w-full h-40 overflow-hidden border border-border select-none",
            config ? (isDragging ? "cursor-grabbing" : "cursor-grab") : "cursor-default",
          ].join(" ")}
        >
          <img
            src={activeBannerUrl}
            alt="Banner preview"
            draggable={false}
            className="absolute inset-0 w-full h-full object-cover"
            style={{ objectPosition: `${posX}% ${posY}%` }}
          />
          {config && !isDragging && (
            <div className="absolute inset-0 flex items-end justify-end p-2 pointer-events-none">
              <span className="font-mono text-[8px] tracking-[0.18em] uppercase text-white/60 bg-black/40 px-2 py-1 backdrop-blur-sm">
                drag to reposition
              </span>
            </div>
          )}
        </div>

        {/* Position readout */}
        {config && (
          <p className="font-mono text-[9px] text-muted-foreground/30 tracking-wider">
            POSITION {Math.round(posX)}% / {Math.round(posY)}%
          </p>
        )}

        <div className="flex items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            className="hidden"
            onChange={handleFileChange}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={uploading}
            className="font-mono text-[10px] tracking-wider px-4 py-2 border border-border text-muted-foreground hover:text-foreground hover:border-primary transition-colors disabled:opacity-40"
          >
            {uploading ? "UPLOADING..." : "UPLOAD IMAGE"}
          </button>
          <button
            onClick={handleRemove}
            disabled={!config}
            className="font-mono text-[10px] tracking-wider px-4 py-2 border border-border text-muted-foreground/50 hover:text-destructive hover:border-destructive transition-colors disabled:opacity-30"
          >
            RESET TO DEFAULT
          </button>
        </div>

        {error && (
          <p className="font-mono text-[10px] text-destructive tracking-wider">{error}</p>
        )}
      </section>
    </div>
  );
}
