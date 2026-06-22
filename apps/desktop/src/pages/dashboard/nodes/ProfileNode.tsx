import type { NodeProps } from "@xyflow/react";
import { DotLoader } from "@anima/standard-templates";
import type { ProfileNode } from "./node-types";
import { NodeShell } from "./NodeShell";

type MediaType = "image" | "gif" | "video";

function getMediaType(url: string): MediaType {
  const lower = url.toLowerCase();
  if (
    lower.includes("data:video/") ||
    /\.(mp4|webm|mov|mkv|avi)(\?.*)?$/.test(lower)
  ) {
    return "video";
  }
  if (lower.includes("data:image/gif") || /\.gif(\?.*)?$/.test(lower)) {
    return "gif";
  }
  return "image";
}

export function ProfileNode({ data }: NodeProps<ProfileNode>) {
  const {
    agentName,
    avatarUrl,
    relationship,
    emotion,
    mood,
    brief,
    briefLoading,
    todayContextLine,
    onExplore,
    onClose,
  } = data;

  const mediaType = getMediaType(avatarUrl);
  const currentThought =
    brief?.message ?? mood?.synthesizedContext ?? todayContextLine;
  const pills = currentThought === brief?.message ? brief?.pills ?? [] : [];

  const media = (
    <div className="h-80 bg-background overflow-hidden">
      {mediaType === "video" ? (
        <video
          src={avatarUrl}
          autoPlay
          muted
          loop
          playsInline
          className="w-full h-full object-cover object-top"
        />
      ) : (
        <img
          src={avatarUrl}
          alt={agentName}
          className="w-full h-full object-cover object-top"
        />
      )}
      <div className="absolute inset-0 bg-gradient-to-t from-background/95 via-background/20 to-transparent" />
      <div className="absolute bottom-0 left-0 right-0 px-4 pb-3 flex items-end justify-between gap-2">
        <div className="min-w-0">
          <span className="text-base font-semibold tracking-tight text-foreground/90 block leading-tight truncate">
            {agentName}
          </span>
          {emotion && (
            <span className="font-mono text-[9px] tracking-[0.18em] uppercase text-foreground/45 block mt-0.5">
              {emotion}
            </span>
          )}
        </div>
        {relationship && (
          <span className="font-mono text-[8px] tracking-[0.2em] uppercase text-muted-foreground/55 border border-border/50 bg-background/50 backdrop-blur-sm px-1.5 py-0.5 rounded-sm shrink-0">
            {relationship}
          </span>
        )}
      </div>
    </div>
  );

  return (
    <NodeShell
      hideHeader
      media={media}
      onClose={onClose}
      className="w-80"
    >
      <div className="px-4 py-4 space-y-3">
        <div>
          <span className="font-mono text-[8px] tracking-[0.25em] uppercase text-muted-foreground/35 block mb-1.5">
            on my mind
          </span>
          <div className="min-h-[2.5rem]">
            {briefLoading ? (
              <DotLoader />
            ) : currentThought ? (
              <p className="text-xs italic text-foreground/65 leading-relaxed">
                {currentThought}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground/20 italic">...</p>
            )}
          </div>
          {!briefLoading && currentThought && pills.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-2">
              {pills.map((pill) => (
                <span
                  key={`${pill.kind}:${pill.label}`}
                  className="font-mono text-[8px] tracking-[0.15em] uppercase text-muted-foreground/40 border border-border/40 px-1.5 py-0.5 rounded-sm"
                >
                  {pill.label}
                </span>
              ))}
            </div>
          )}
        </div>

        <div className="flex justify-end pt-0.5">
          <button
            onClick={() => onExplore(currentThought ?? "", pills)}
            className="font-mono text-[9px] tracking-[0.18em] uppercase text-muted-foreground/50 hover:text-foreground border border-border/35 hover:border-border/70 px-2.5 py-1 rounded transition-all duration-150"
          >
            explore →
          </button>
        </div>
      </div>
    </NodeShell>
  );
}
