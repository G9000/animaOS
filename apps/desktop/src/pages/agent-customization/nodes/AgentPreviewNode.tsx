import { useEffect } from "react";
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type NodeProps,
} from "@xyflow/react";
import { NodeShell } from "../../dashboard/nodes/NodeShell";
import type { BiographyPreviewNode } from "./types";
import { DocumentIcon } from "@anima/standard-templates";

const TARGET_HANDLE: React.CSSProperties = {
  position: "absolute",
  left: 0,
  top: "50%",
  transform: "translate(-50%, -50%)",
  width: 16,
  height: 16,
  background: "var(--accent)",
  borderRadius: "50%",
  border: "3px solid var(--background)",
  zIndex: 9999,
  pointerEvents: "none",
  animation: "handle-pulse 2.4s ease-out infinite",
};

function excerpt(value: string, fallback: string): string {
  const trimmed = value.trim();
  if (!trimmed) return fallback;
  return trimmed.length > 180 ? `${trimmed.slice(0, 177).trim()}...` : trimmed;
}

function formatAgentBirthday(value: string): string {
  if (!value) return "Not recorded";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;

  const pad = (part: number) => part.toString().padStart(2, "0");
  return [
    `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())}`,
    `${pad(parsed.getHours())}:${pad(parsed.getMinutes())}:${pad(parsed.getSeconds())}`,
  ].join(" ");
}

function sectionContent(
  sections: BiographyPreviewNode["data"]["previewSections"],
  id: string,
): string {
  return sections.find((s) => s.id === id)?.content ?? "";
}

function optionalSection(content: string, fallback: string): string {
  return content.trim() || fallback.trim();
}

export function AgentPreviewNode({ data, id }: NodeProps<BiographyPreviewNode>) {
  const updateNodeInternals = useUpdateNodeInternals();
  useEffect(() => { updateNodeInternals(id); }, [id, updateNodeInternals]);

  const {
    avatarUrl, agentName, relationship, dominantEmotion,
    identityDraft, personaDraft, originDraft, directiveDraft, autonomyDraft, revisionDraft, intentionsDraft,
    agentBirthday, previewSections, onClose,
  } = data;

  const identity = identityDraft || sectionContent(previewSections, "identity");
  const persona = personaDraft || sectionContent(previewSections, "persona");
  const origin = optionalSection(originDraft, sectionContent(previewSections, "origin"));
  const directive = optionalSection(directiveDraft, sectionContent(previewSections, "user_directive"));
  const autonomy = autonomyDraft.trim();
  const revision = revisionDraft.trim();
  const intentions = optionalSection(intentionsDraft, sectionContent(previewSections, "intentions"));
  const optionalProfileSections = [
    { title: "Origin Story", content: origin },
    { title: "Agent Directive", content: directive },
    { title: "Autonomy Policy", content: autonomy },
    { title: "Self-Revision Inbox", content: revision },
    { title: "Active Intentions", content: intentions },
  ].filter((section) => section.content.trim());

  return (
    <div style={{ position: "relative" }} className="agent-preview-wrapper">
      <NodeShell title="Biography" icon={<DocumentIcon size="sm" className="text-foreground/25" />} onClose={onClose} required className="w-[340px]">
        <div className="p-4 space-y-4">
          <div className="flex items-center gap-3">
            <div className="h-12 w-12 shrink-0 overflow-hidden border border-foreground/[0.08] bg-foreground/[0.04]">
              <img src={avatarUrl} alt={agentName} className="h-full w-full object-cover" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-[14px] font-semibold leading-snug text-foreground truncate">
                {agentName || "Anima"}
              </p>
              {(dominantEmotion || relationship) && (
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {dominantEmotion && (
                    <span className="font-mono text-[7px] tracking-[0.18em] uppercase px-1.5 py-0.5 border border-accent/35 text-accent/70">
                      {dominantEmotion}
                    </span>
                  )}
                  {relationship && (
                    <span className="font-mono text-[7px] tracking-[0.16em] uppercase px-1.5 py-0.5 bg-foreground/[0.04] text-foreground/40 border border-foreground/[0.06]">
                      {relationship}
                    </span>
                  )}
                </div>
              )}
            </div>
          </div>

          <div className="space-y-3">
            <section
              className="pl-3"
              style={{ borderLeft: "2px solid color-mix(in oklch, var(--accent) 32%, transparent)" }}
            >
              <h3 className="font-mono text-[7.5px] tracking-[0.26em] uppercase text-accent/55 mb-1.5">
                Core Identity
              </h3>
              <p className="text-[11px] leading-relaxed text-foreground/60 line-clamp-3">
                {excerpt(identity, "Identity has not been written yet.")}
              </p>
            </section>

            <section
              className="pl-3"
              style={{ borderLeft: "2px solid color-mix(in oklch, var(--accent) 20%, transparent)" }}
            >
              <h3 className="font-mono text-[7.5px] tracking-[0.26em] uppercase text-accent/45 mb-1.5">
                Voice & Persona
              </h3>
              <p className="text-[11px] leading-relaxed text-foreground/60 line-clamp-3">
                {excerpt(persona, "Voice and persona have not been written yet.")}
              </p>
            </section>

            <div
              className="flex items-center justify-between gap-3 pt-2.5"
              style={{ borderTop: "1px solid color-mix(in oklch, var(--foreground) 6%, transparent)" }}
            >
              <span className="font-mono text-[7.5px] tracking-[0.26em] uppercase text-foreground/25">
                Agent Birthday
              </span>
              <span className="font-mono text-[9.5px] text-foreground/50 text-right">
                {formatAgentBirthday(agentBirthday)}
              </span>
            </div>

            {optionalProfileSections.length > 0 && (
              <div
                className="space-y-2.5 pt-2.5"
                style={{ borderTop: "1px solid color-mix(in oklch, var(--foreground) 6%, transparent)" }}
              >
                {optionalProfileSections.map((section) => (
                  <section key={section.title} className="pl-3 border-l border-foreground/[0.08]">
                    <h3 className="font-mono text-[7.5px] tracking-[0.22em] uppercase text-foreground/35 mb-1">
                      {section.title}
                    </h3>
                    <p className="text-[10.5px] leading-relaxed text-foreground/55 line-clamp-2">
                      {excerpt(section.content, "")}
                    </p>
                  </section>
                ))}
              </div>
            )}
          </div>
        </div>
      </NodeShell>

      <Handle type="target" position={Position.Left} style={TARGET_HANDLE} />
    </div>
  );
}
