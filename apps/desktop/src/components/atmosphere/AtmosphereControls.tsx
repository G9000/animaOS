import { useState } from "react";
import {
  Button,
  SpeakerOnIcon,
  SpeakerOffIcon,
  ImageIcon,
  cn,
} from "@anima/standard-templates";
import { useBgmPlayer } from "../../hooks/useBgmPlayer";
import { BgmPanel } from "./BgmPanel";
import { AsciiPanel } from "./AsciiPanel";

type Panel = "bgm" | "ascii" | null;

export function AtmosphereControls() {
  const bgm = useBgmPlayer();
  const [panel, setPanel] = useState<Panel>(null);
  const toggle = (p: Panel) => setPanel((cur) => (cur === p ? null : p));

  const track = bgm.currentTrack;

  return (
    <div className="fixed top-4 right-4 z-50 flex flex-col items-end gap-2">
      <div className={cn("flex items-stretch h-9 bg-background")}>
        <Button
          variant="slide"
          size="xs"
          onClick={() => toggle("bgm")}
          className="h-full gap-2 px-3 rounded-none"
        >
          <span
            className="font-mono text-[9px] select-none text-muted-foreground transition-colors group-hover:text-accent-foreground"
          >
            ♫
          </span>
          <span className="flex items-baseline gap-1 select-none">
            {track?.trackNum && (
              <span className="font-mono text-[7.5px] tracking-[0.12em] text-muted-foreground uppercase transition-colors group-hover:text-accent-foreground">
                {track.trackNum}
              </span>
            )}
            <span
              className={cn(
                "font-mono text-[7.5px] tracking-[0.14em] uppercase transition-colors duration-200 group-hover:text-accent-foreground text-muted-foreground",
            
              )}
            >
              {track?.name ?? "bgm"}
            </span>
          </span>
        </Button>

        {/* Mute toggle */}
        <Button
          variant="slide"
          size="md"
          iconOnly
          icon={
            bgm.muted ? (
              <SpeakerOffIcon size="sm" />
            ) : (
              <SpeakerOnIcon size="sm" />
            )
          }
          onClick={bgm.toggleMute}
          title={bgm.muted ? "Unmute BGM" : "Mute BGM"}
          className={cn(
            "rounded-none text-muted-foreground",
         
          )}
        />

        <span className="w-px bg-foreground/[0.08] self-stretch" />

        {/* Background / ASCII settings toggle */}
        <Button
          variant="slide"
          size="md"
          iconOnly
          icon={<ImageIcon size="sm" />}
          onClick={() => toggle("ascii")}
          title="Background settings"
          className="rounded-none text-muted-foreground"
        />
      </div>

      {panel === "bgm" && <BgmPanel bgm={bgm} className="bg-background" />}
      {panel === "ascii" && <AsciiPanel className="bg-background" />}
    </div>
  );
}
