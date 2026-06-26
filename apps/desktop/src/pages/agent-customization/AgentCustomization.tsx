import { useCallback, useState } from "react";
import { ReactFlow } from "@xyflow/react";
import { ChevronRightIcon, cn } from "@anima/standard-templates";
import { edgeTypes, nodeTypes, type AgentNode } from "./nodes";
import { useAgentNodes } from "./hooks/useAgentNodes";

const RAIL_STORAGE_KEY = "anima-agent-rail-collapsed";

function readCollapsedState(): boolean {
  try {
    return localStorage.getItem(RAIL_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function persistCollapsedState(collapsed: boolean): void {
  try {
    localStorage.setItem(RAIL_STORAGE_KEY, String(collapsed));
  } catch {
    // ignore
  }
}

export default function AgentCustomization() {
  const {
    nodes,
    edges,
    onNodesChange,
    fileInputRef,
    handleFileChange,
    optionalNodeToggles,
  } = useAgentNodes();

  const [collapsed, setCollapsed] = useState(readCollapsedState);
  const toggleCollapsed = useCallback(() => {
    setCollapsed((current) => {
      const next = !current;
      persistCollapsedState(next);
      return next;
    });
  }, []);

  const [search, setSearch] = useState("");
  const filteredToggles = search.trim()
    ? optionalNodeToggles.filter((t) =>
        t.label.toLowerCase().includes(search.toLowerCase()) ||
        t.description.toLowerCase().includes(search.toLowerCase()),
      )
    : optionalNodeToggles;

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
        <aside
          className={cn(
            "agent-optional-rail relative z-10 flex shrink-0 flex-col border-r border-foreground/[0.06] bg-background/25 backdrop-blur-[40px] shadow-[4px_0_24px_rgba(0,0,0,0.12)] overflow-hidden",
            "transition-[width] duration-200 ease-[cubic-bezier(0.16,1,0.3,1)]",
            collapsed ? "w-14" : "w-60",
          )}
          style={{ paddingTop: '96px' }}
        >
          {!collapsed && (
            <>
              <div className="px-4 pt-5 pb-2 shrink-0">
                <p className="font-mono text-[8px] tracking-[0.32em] uppercase text-foreground/20 select-none">
                  OPTIONAL
                </p>
              </div>
              <div className="px-3 pb-2 shrink-0">
                <input
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="search…"
                  className="nodrag w-full bg-foreground/[0.04] border border-foreground/[0.08] px-2 py-1 font-mono text-[9px] text-foreground/60 placeholder:text-foreground/20 outline-none focus:border-foreground/20 transition-colors"
                />
              </div>
              <div className="h-px bg-foreground/[0.06] mx-3 shrink-0" />
            </>
          )}

          <nav className="flex-1 overflow-y-auto overflow-x-hidden py-2 space-y-px">
            {!collapsed && filteredToggles.length === 0 && (
              <p className="px-3 py-2 font-mono text-[9px] text-foreground/25">no match</p>
            )}
            {!collapsed && filteredToggles.map((toggle) => (
              <button
                key={toggle.id}
                type="button"
                onClick={toggle.onToggle}
                aria-pressed={toggle.active}
                data-dangerous={toggle.dangerous}
                className="nodrag group block w-full text-left px-2"
                title={toggle.dangerous ? "Requires identity override to edit" : "Read-only profile history"}
              >
                <div
                  className={cn(
                    "relative flex flex-col gap-0.5 px-3 py-2.5 transition-all duration-150",
                    toggle.active
                      ? "bg-accent"
                      : "group-hover:bg-foreground/[0.06]",
                  )}
                  style={toggle.active ? {
                    transform: "translateX(4px)",
                    boxShadow: "-2px 2px 0 var(--color-accent-dark), -4px 4px 0 color-mix(in oklch, var(--color-accent-dark) 55%, transparent)",
                  } : undefined}
                  onMouseEnter={(e) => {
                    if (!toggle.active) {
                      const el = e.currentTarget as HTMLElement;
                      el.style.transform = "translateX(4px)";
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
                    toggle.active ? "text-background font-semibold" : "text-foreground/65",
                  )}>
                    {toggle.label}
                  </span>
                  <span className={cn(
                    "font-mono text-[9px] mt-0.5 leading-snug tracking-wide truncate",
                    toggle.active ? "text-background/75" : "text-foreground/40",
                  )}>
                    {toggle.description}
                  </span>
                </div>
              </button>
            ))}
          </nav>

          {/* Footer — collapse toggle */}
          <div className="shrink-0 border-t border-foreground/[0.06] p-1.5">
            <button
              type="button"
              onClick={toggleCollapsed}
              title={collapsed ? "Expand (Ctrl+/)" : "Collapse (Ctrl+/)"}
              className={cn(
                "nodrag flex items-center justify-center gap-2 py-2 w-full text-foreground/30 hover:text-foreground/70 hover:bg-foreground/[0.06] transition-colors",
              )}
            >
              <ChevronRightIcon
                size="sm"
                className={cn("transition-transform duration-200", !collapsed && "rotate-180")}
              />
              {!collapsed && (
                <span className="text-caption font-mono tracking-[0.16em] uppercase">Collapse</span>
              )}
            </button>
          </div>
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
