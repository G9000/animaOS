import { ReactFlow } from "@xyflow/react";
import { cn } from "@anima/standard-templates";
import { edgeTypes, nodeTypes, type AgentNode } from "./nodes";
import { useAgentNodes } from "./hooks/useAgentNodes";

export default function AgentCustomization() {
  const {
    nodes,
    edges,
    onNodesChange,
    fileInputRef,
    handleFileChange,
    optionalNodeToggles,
  } = useAgentNodes();

  return (
    <>
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml"
        className="hidden"
        onChange={handleFileChange}
      />

      <div className="relative size-full flex overflow-hidden">

        {/* ── Optional node rail ── */}
        <aside className="agent-optional-rail relative z-10 flex w-40 shrink-0 flex-col border-r border-foreground/[0.06] bg-background/25 backdrop-blur-[40px] shadow-[4px_0_24px_rgba(0,0,0,0.12)] overflow-visible" style={{ paddingTop: '96px' }}>
          <div className="px-4 pt-5 pb-3 shrink-0">
            <p className="font-mono text-[8px] tracking-[0.32em] uppercase text-foreground/20 select-none">
              OPTIONAL
            </p>
          </div>
          <div className="h-px bg-foreground/[0.06] mx-3 shrink-0" />
          <nav className="flex-1 px-2 py-2 space-y-px overflow-visible">
            {optionalNodeToggles.map((toggle) => (
              <button
                key={toggle.id}
                type="button"
                onClick={toggle.onToggle}
                aria-pressed={toggle.active}
                data-dangerous={toggle.dangerous}
                className="nodrag group block w-full text-left"
                title={toggle.dangerous ? "Requires identity override to edit" : "Read-only profile history"}
              >
                <div
                  className={cn(
                    "relative flex flex-col gap-0.5 px-3 py-2.5 transition-all duration-150",
                    toggle.active
                      ? "bg-accent z-20"
                      : cn(
                          "z-10",
                          "group-hover:bg-foreground/[0.06]",
                        ),
                  )}
                  style={toggle.active ? {
                    transform: "translateX(12px)",
                    boxShadow: "-2px 2px 0 var(--color-accent-dark), -4px 4px 0 color-mix(in oklch, var(--color-accent-dark) 55%, transparent)",
                  } : undefined}
                  onMouseEnter={(e) => {
                    if (!toggle.active) {
                      const el = e.currentTarget as HTMLElement;
                      el.style.transform = "translateX(6px)";
                      el.style.boxShadow = "-2px 2px 0 rgba(0,0,0,0.3), -3px 3px 0 rgba(0,0,0,0.15)";
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!toggle.active) {
                      const el = e.currentTarget as HTMLElement;
                      el.style.transform = "";
                      el.style.boxShadow = "";
                    }
                  }}
                >
                  <span className={cn(
                    "font-mono text-[11px] tracking-[0.12em] uppercase transition-colors",
                    toggle.active
                      ? "text-background font-semibold"
                      : "text-foreground/65",
                  )}>
                    {toggle.label}
                  </span>
                  <span className={cn(
                    "font-mono text-[9px] mt-0.5 leading-snug tracking-wide truncate",
                    toggle.active
                      ? "text-background/75"
                      : "text-foreground/40",
                  )}>
                    {toggle.description}
                  </span>
                </div>
              </button>
            ))}
          </nav>
        </aside>

        {/* ── Canvas ── */}
        <div className="flex-1 min-h-0">
          <ReactFlow<AgentNode>
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            nodesDraggable
            nodesConnectable={false}
            elementsSelectable={false}
            connectOnClick={false}
            fitView
            fitViewOptions={{ padding: 0.18 }}
            minZoom={0.3}
            maxZoom={1.5}
            colorMode="dark"
            className="agent-customization-flow size-full"
            style={{ background: "transparent" }}
          />
        </div>

      </div>
    </>
  );
}
