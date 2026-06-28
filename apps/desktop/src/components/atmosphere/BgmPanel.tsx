import { useRef } from "react";
import { XIcon, cn } from "@anima/standard-templates";
import { useBgmPlayer } from "../../hooks/useBgmPlayer";

type BgmState = ReturnType<typeof useBgmPlayer>;

interface BgmPanelProps {
  bgm: BgmState;
  className?: string;
}

export function BgmPanel({ bgm, className }: BgmPanelProps) {
  const fileRef = useRef<HTMLInputElement>(null);

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    await bgm.addTrack(file);
    e.target.value = "";
  }

  return (
    <div className={cn("w-72 py-2 flex flex-col", className)}>
      {bgm.tracks.map(t => (
        <div
          key={t.id}
          onClick={() => bgm.selectTrack(t.id)}
          className={cn(
            "group flex items-center gap-2.5 px-4 py-2 transition-colors duration-100 cursor-pointer",
            t.id === bgm.currentId ? "text-accent" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <span className="font-mono text-[9px] w-3 shrink-0">
            {t.id === bgm.currentId ? "●" : "○"}
          </span>
          <div className="flex items-baseline gap-1.5 flex-1 min-w-0">
            {t.trackNum && (
              <span className="font-mono text-[7.5px] tracking-[0.1em] opacity-50 shrink-0">
                {t.trackNum}
              </span>
            )}
            <span className="font-mono text-[8px] tracking-[0.12em] uppercase truncate">
              {t.name}
            </span>
          </div>
          {!t.builtIn && (
            <button
              onClick={e => { e.stopPropagation(); bgm.removeTrack(t.id); }}
              className="opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-all shrink-0"
            >
              <XIcon size="sm" />
            </button>
          )}
        </div>
      ))}

      <div className="mx-4 my-1.5 h-px bg-foreground/[0.08]" />

      <div className="px-4 py-1 flex flex-col gap-1">
        <button
          onClick={() => fileRef.current?.click()}
          className="font-mono text-[8px] tracking-[0.14em] uppercase text-muted-foreground hover:text-foreground text-left py-1.5 transition-colors"
        >
          ↑ add your own audio
        </button>
        <input ref={fileRef} type="file" accept="audio/*" className="hidden" onChange={handleFile} />
        <button
          onClick={bgm.resetBgm}
          className="font-mono text-[8px] tracking-[0.14em] uppercase text-muted-foreground/70 hover:text-muted-foreground text-left py-1.5 transition-colors"
        >
          ↺ reset to defaults
        </button>
      </div>
    </div>
  );
}
