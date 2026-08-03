import type { NodeProps } from "@xyflow/react";
import { DotLoader } from "@anima/standard-templates";
import type { ProfileNode } from "./node-types";
import { useDreamShownReceipt } from "./useDreamShownReceipt";

type MediaType = "image" | "gif" | "video";

function getMediaType(url: string): MediaType {
  const lower = url.toLowerCase();
  if (
    lower.includes("data:video/") ||
    /\.(mp4|webm|mov|mkv|avi)(\?.*)?$/.test(lower)
  )
    return "video";
  if (lower.includes("data:image/gif") || /\.gif(\?.*)?$/.test(lower))
    return "gif";
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
    onDreamShown,
  } = data;

  const mediaType = getMediaType(avatarUrl);
  const currentThought =
    brief?.message ?? mood?.synthesizedContext ?? todayContextLine;
  const pills = currentThought === brief?.message ? (brief?.pills ?? []) : [];
  // IL-015 (PR #135 review, P1): the dream counts as shown only once this
  // node has actually rendered the greeting text — the same condition the
  // markup below uses. See GreetingNode for why the fetch handler is the
  // wrong place to acknowledge from.
  const dreamVisible =
    Boolean(brief?.ambientDream) &&
    !briefLoading &&
    currentThought === brief?.message;
  const profileRef = useDreamShownReceipt<HTMLParagraphElement>(
    dreamVisible,
    onDreamShown,
  );

  return (
    <div className="group relative w-80 overflow-visible">

      {/* Close — floats above card, fades in on hover */}
      <button
        onClick={onClose}
        className="absolute -top-5 right-0 z-20 h-4 px-1.5 flex items-center rounded-sm bg-background/60 border border-hairline-faint font-mono text-micro text-foreground/25 hover:text-foreground/60 hover:bg-background/80 opacity-0 group-hover:opacity-100 transition-all duration-200 backdrop-blur-sm"
        aria-label="Close widget"
      >
        ×
      </button>

      {/* Glass card */}
      <div className="overflow-hidden rounded-xl bg-background/20 backdrop-blur-[36px] border border-hairline shadow-[0_6px_32px_rgba(0,0,0,0.22)]">

        {/* ── Media ── */}
        <div className="relative h-72 bg-background overflow-hidden">
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

          {/* Scrim — uses background token so it blends into the card and adapts to theme */}
          <div className="absolute inset-0 bg-gradient-to-t from-background/95 via-background/15 to-transparent" />

          {/* Identity overlay */}
          <div className="absolute bottom-0 left-0 right-0 px-4 pb-4 flex items-end justify-between gap-3">
            <div className="min-w-0">
              <span className="text-[17px] font-semibold tracking-tight text-foreground/90 block leading-tight truncate">
                {agentName}
              </span>
              {emotion && (
                <span className="flex items-center gap-1.5 mt-1">

                  <span className="font-mono text-micro tracking-caps-4 uppercase text-foreground/45">
                    {emotion}
                  </span>
                </span>
              )}
            </div>
            {relationship && (
              <span className="font-mono text-nano tracking-caps-4 uppercase text-foreground/40 border border-hairline bg-foreground/[0.06] backdrop-blur-sm px-2 py-0.5 rounded-sm shrink-0">
                {relationship}
              </span>
            )}
          </div>
        </div>

        {/* ── Content ── */}
        <div className="px-4 py-4 space-y-3">

          {/* On my mind */}
          <div>
            <span className="font-mono text-[7.5px] tracking-caps-5 uppercase text-foreground/22 block mb-2">
              on my mind
            </span>
            <div className="min-h-[2.5rem]">
              {briefLoading ? (
                <DotLoader />
              ) : currentThought ? (
                <p
                  ref={profileRef}
                  className="text-detail italic text-foreground/58 leading-relaxed"
                >
                  {currentThought}
                </p>
              ) : (
                <p className="text-xs text-foreground/15 italic">…</p>
              )}
            </div>
          </div>

          {/* Pills — each clickable to explore that topic */}
          {!briefLoading && pills.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {pills.map((pill) => (
                <button
                  key={`${pill.kind}:${pill.label}`}
                  onClick={() => onExplore(pill.label, [pill])}
                  className="font-mono text-[7.5px] tracking-caps-2 uppercase text-foreground/35 border border-hairline hover:border-foreground/22 hover:text-foreground/65 px-1.5 py-0.5 rounded-sm transition-all duration-150 bg-foreground/[0.02] hover:bg-foreground/[0.05]"
                >
                  {pill.label}
                </button>
              ))}
            </div>
          )}

          {/* Explore */}
          <div className="flex justify-end pt-0.5">
            <button
              onClick={() => onExplore(currentThought ?? "", pills)}
              disabled={!currentThought && !briefLoading}
              className="font-mono text-micro tracking-caps-4 uppercase text-foreground/40 hover:text-foreground/80 border border-hairline hover:border-foreground/25 bg-foreground/[0.02] hover:bg-foreground/[0.05] px-3 py-1 rounded-sm transition-all duration-150 disabled:opacity-25 disabled:cursor-default"
            >
              explore →
            </button>
          </div>

        </div>
      </div>
    </div>
  );
}
