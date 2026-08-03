import type { NodeProps } from "@xyflow/react";
import { DotLoader } from "@anima/standard-templates";
import type { GreetingNode } from "./node-types";
import { useDreamShownReceipt } from "./useDreamShownReceipt";

export function GreetingNode({ data }: NodeProps<GreetingNode>) {
  const { agentName, brief, briefLoading, userName, onChat, onClose, onDreamShown } =
    data;
  const firstName = userName?.split(" ")[0];
  const message =
    brief?.message ??
    `Hi${firstName ? ` ${firstName}` : ""}, how can I help you today?`;
  const pills = brief?.pills ?? [];
  // IL-015 (PR #135 review, P1): report the dream as SHOWN only from here,
  // after the commit that puts this text on screen AND only while the page
  // is visible. This node can be closed, and the window can be in the
  // background — acknowledging from the fetch handler marked dreams
  // surfaced that nothing on screen ever displayed, the exact loss IL-015
  // exists to prevent.
  const dreamVisible = Boolean(brief?.ambientDream) && !briefLoading;
  const greetingRef = useDreamShownReceipt<HTMLParagraphElement>(
    dreamVisible,
    onDreamShown,
  );

  return (
    <div className="group relative w-72 overflow-visible">

      {/* Close */}
      <button
        onClick={onClose}
        className="absolute -top-5 right-0 z-20 h-4 px-1.5 flex items-center rounded-sm bg-background/60 border border-foreground/[0.07] font-mono text-[8px] text-foreground/25 hover:text-foreground/60 hover:bg-background/80 opacity-0 group-hover:opacity-100 transition-all duration-200 backdrop-blur-sm"
        aria-label="Close greeting"
      >
        ×
      </button>

      {/* Glass card */}
      <div className="rounded-xl bg-background/25 backdrop-blur-[36px] border border-foreground/[0.08] shadow-[0_4px_28px_rgba(0,0,0,0.18)] px-5 py-5">
        {briefLoading ? (
          <div className="py-4">
            <DotLoader />
          </div>
        ) : (
          <div className="space-y-3 animate-fade-in">

            {/* Attribution */}
            {brief?.llmGenerated && (
              <span className="font-mono text-[7.5px] tracking-[0.3em] uppercase text-foreground/22 block">
                from {agentName}
              </span>
            )}

            {/* Message — the receipt ref goes HERE, on the text itself, so
                an overlay covering the sentence is not missed by a check
                against the whole card (PR #135 review). */}
            <p
              ref={greetingRef}
              className="text-[17px] font-sans text-foreground/85 leading-snug"
            >
              {message}
            </p>

            {/* Pills — informational tags */}
            {pills.length > 0 && (
              <div className="flex flex-wrap gap-1 pt-0.5">
                {pills.map((pill) => (
                  <span
                    key={`${pill.kind}:${pill.label}`}
                    className="font-mono text-[7.5px] tracking-[0.12em] uppercase text-foreground/30 border border-foreground/[0.08] bg-foreground/[0.02] px-1.5 py-0.5 rounded-sm"
                  >
                    {pill.label}
                  </span>
                ))}
              </div>
            )}

            {/* CTA */}
            <div className="flex justify-end pt-1">
              <button
                onClick={onChat}
                className="font-mono text-[8px] tracking-[0.2em] uppercase text-foreground/40 hover:text-foreground/80 border border-foreground/[0.09] hover:border-foreground/25 bg-foreground/[0.02] hover:bg-foreground/[0.05] px-3 py-1 rounded-sm transition-all duration-150"
              >
                start chat →
              </button>
            </div>

          </div>
        )}
      </div>
    </div>
  );
}
