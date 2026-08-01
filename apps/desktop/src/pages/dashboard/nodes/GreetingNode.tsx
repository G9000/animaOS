import type { NodeProps } from "@xyflow/react";
import { DotLoader, cn, glassPanel } from "@anima/standard-templates";
import type { GreetingNode } from "./node-types";

export function GreetingNode({ data }: NodeProps<GreetingNode>) {
  const { agentName, brief, briefLoading, userName, onChat, onClose } = data;
  const firstName = userName?.split(" ")[0];
  const message =
    brief?.message ??
    `Hi${firstName ? ` ${firstName}` : ""}, how can I help you today?`;
  const pills = brief?.pills ?? [];

  return (
    <div className="group relative w-72 overflow-visible">

      {/* Close */}
      <button
        onClick={onClose}
        className="absolute -top-5 right-0 z-20 h-4 px-1.5 flex items-center rounded-sm bg-background/60 border border-hairline-faint font-mono text-micro text-foreground/25 hover:text-foreground/60 hover:bg-background/80 opacity-0 group-hover:opacity-100 transition-all duration-200 backdrop-blur-sm"
        aria-label="Close greeting"
      >
        ×
      </button>

      {/* Glass card */}
      <div className={cn(glassPanel, "px-5 py-5")}>
        {briefLoading ? (
          <div className="py-4">
            <DotLoader />
          </div>
        ) : (
          <div className="space-y-3 animate-fade-in">

            {/* Attribution */}
            {brief?.llmGenerated && (
              <span className="font-mono text-[7.5px] tracking-caps-5 uppercase text-foreground/22 block">
                from {agentName}
              </span>
            )}

            {/* Message */}
            <p className="text-[17px] font-sans text-foreground/85 leading-snug">
              {message}
            </p>

            {/* Pills — informational tags */}
            {pills.length > 0 && (
              <div className="flex flex-wrap gap-1 pt-0.5">
                {pills.map((pill) => (
                  <span
                    key={`${pill.kind}:${pill.label}`}
                    className="font-mono text-[7.5px] tracking-caps-2 uppercase text-foreground/30 border border-hairline bg-foreground/[0.02] px-1.5 py-0.5 rounded-sm"
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
                className="font-mono text-micro tracking-caps-4 uppercase text-foreground/40 hover:text-foreground/80 border border-hairline hover:border-foreground/25 bg-foreground/[0.02] hover:bg-foreground/[0.05] px-3 py-1 rounded-sm transition-all duration-150"
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
