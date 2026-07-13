import { useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { Handle, NodeResizer, Position, useUpdateNodeInternals, type NodeProps } from "@xyflow/react";
import {
  SecurityIcon,
  MicIcon,
  SparkleIcon,
  CompassIcon,
  GearIcon,
  PlusIcon,
  XIcon,
} from "@anima/standard-templates";
import { NodeShell, type NodeAction } from "@anima/standard-templates";
import { TA, type TextNode } from "./types";

const IC = "text-foreground/25";

const TITLE_ICONS: Record<string, ReactNode> = {
  "Core Identity":     <SecurityIcon  size="sm" className={IC} />,
  "Voice & Persona":   <MicIcon       size="sm" className={IC} />,
  "Origin Story":      <SparkleIcon   size="sm" className={IC} />,
  "Agent Directive":   <CompassIcon   size="sm" className={IC} />,
  "Autonomy Policy":   <GearIcon      size="sm" className={IC} />,
};

const AT_RIGHT: React.CSSProperties = {
  position: "absolute",
  right: 0,
  top: "50%",
  transform: "translate(50%, -50%)",
  pointerEvents: "none",
};

export function AgentTextNode({ data, id }: NodeProps<TextNode>) {
  const updateNodeInternals = useUpdateNodeInternals();
  const listInputRefs = useRef<Array<HTMLInputElement | null>>([]);
  useEffect(() => { updateNodeInternals(id); }, [id, updateNodeInternals]);

  const {
    nodeTitle, description, draft, version, loading,
    saving, generating, saved, hasWarmer, readOnly, required, listEditor, requiresOverride,
    identityOverrideAllowed, overrideDescription,
    onChange, onSave, onThinkingMonologueGenerate, onWarmer, onIdentityOverrideAllowedChange, onClose,
    cardWidth, inputRows,
  } = data;

  const isLarge = (inputRows ?? 6) > 6;
  const lockedByOverride = Boolean(requiresOverride && !identityOverrideAllowed);
  const canEdit = !readOnly && !lockedByOverride;

  const actions: NodeAction[] = [
    ...(hasWarmer && canEdit ? [{ id: "warmer", label: "Warmer", onClick: onWarmer }] : []),
    ...(onThinkingMonologueGenerate && canEdit ? [{ id: "generate", label: generating ? "Generating..." : "Generate", onClick: onThinkingMonologueGenerate }] : []),
    ...(canEdit ? [{ id: "save", label: saving ? "Saving..." : "Save", onClick: onSave }] : []),
  ];

  const taClass = isLarge
    ? [
        "nodrag nowheel w-full resize-none flex-1 min-h-0",
        "bg-foreground/[0.04] border border-foreground/[0.08]",
        "px-4 py-3.5 font-mono text-[13px] leading-relaxed text-foreground/80",
        "outline-none focus:border-foreground/20 transition-colors",
        "placeholder:text-foreground/[0.18] disabled:opacity-40",
      ].join(" ")
    : TA;

  const listRows = listEditor ? draft.split("\n") : [];
  const listControlsDisabled = loading || saving || !canEdit;
  const setListRows = (rows: string[]) => onChange(rows.join("\n"));
  const focusListRow = (index: number) => {
    window.setTimeout(() => listInputRefs.current[index]?.focus(), 0);
  };
  const updateListRow = (index: number, value: string) => {
    const next = [...listRows];
    next[index] = value;
    setListRows(next);
  };
  const addListRow = (index = listRows.length - 1) => {
    const next = [...listRows];
    next.splice(index + 1, 0, "");
    setListRows(next);
    focusListRow(index + 1);
  };
  const removeListRow = (index: number) => {
    if (listRows.length <= 1) {
      setListRows([""]);
      focusListRow(0);
      return;
    }
    const next = listRows.filter((_, rowIndex) => rowIndex !== index);
    setListRows(next);
    focusListRow(Math.max(0, index - 1));
  };
  const handleListKeyDown = (
    event: React.KeyboardEvent<HTMLInputElement>,
    index: number,
  ) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addListRow(index);
    }
  };
  const handleListPaste = (
    event: React.ClipboardEvent<HTMLInputElement>,
    index: number,
  ) => {
    const pasted = event.clipboardData.getData("text");
    const pastedRows = pasted
      .replace(/\r/g, "")
      .split("\n")
      .map((line) => line.trim());
    if (pastedRows.length <= 1) return;
    event.preventDefault();
    const next = [...listRows];
    next.splice(index, 1, ...pastedRows);
    setListRows(next);
    focusListRow(index + pastedRows.length - 1);
  };

  return (
    <div style={{ position: "relative", ...(isLarge && { width: "100%", height: "100%" }) }}>
      {isLarge && (
        <NodeResizer
          minWidth={340}
          minHeight={220}
          lineStyle={{ borderColor: "color-mix(in oklch, var(--foreground) 8%, transparent)", borderWidth: 1 }}
          handleStyle={{ width: 7, height: 7, borderRadius: 2, border: "1px solid color-mix(in oklch, var(--foreground) 12%, transparent)", background: "var(--background)", opacity: 0.8 }}
        />
      )}
      <NodeShell
        title={nodeTitle}
        icon={TITLE_ICONS[nodeTitle]}
        headerExtra={
          <div className="flex items-center gap-2">
            {version != null && (
              <span className="font-mono text-[7px] text-muted-foreground/40 border border-border/40 px-1 py-0.5">
                v{version}
              </span>
            )}
            {saved && (
              <span className="font-mono text-[8px] tracking-[0.14em] uppercase text-accent/70">Saved</span>
            )}
          </div>
        }
        onClose={onClose}
        actions={actions}
        required={required ?? true}
        fluid={isLarge}
        className={isLarge ? "w-full h-full" : (cardWidth ?? "w-[340px]")}
        footer={
          <div className="px-3.5 py-2 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2.5">
              {requiresOverride && onIdentityOverrideAllowedChange && (
                <label className="nodrag flex items-center gap-1.5 font-mono text-[8px] tracking-[0.18em] uppercase text-accent/55">
                  <input
                    type="checkbox"
                    checked={identityOverrideAllowed}
                    onChange={(event) => onIdentityOverrideAllowedChange(event.target.checked)}
                    className="h-3 w-3 accent-[var(--accent)]"
                  />
                  Override
                </label>
              )}
              {readOnly && (
                <span className="font-mono text-[8px] tracking-[0.18em] uppercase text-foreground/25">
                  Read only
                </span>
              )}
            </div>
            <div className="flex items-center justify-end gap-2.5">
              <span className="font-mono text-[8px] text-muted-foreground/30">
                {draft.length.toLocaleString()} chars
              </span>
              <span className="font-mono text-[8px] text-muted-foreground/20">/</span>
              <span className="font-mono text-[8px] text-muted-foreground/30">
                ~{Math.round(draft.length / 4).toLocaleString()} tokens
              </span>
            </div>
          </div>
        }
      >
        <div className={isLarge ? "p-4 flex flex-col gap-3 h-full min-h-0" : "p-3 space-y-2"}>
          <p className={isLarge ? "shrink-0 font-mono text-[10px] text-foreground/35 leading-relaxed" : "font-mono text-[9px] text-muted-foreground/50 leading-relaxed"}>
            {description}
          </p>
          {lockedByOverride && (
            <p className="shrink-0 font-mono text-[8.5px] leading-relaxed text-accent/50">
              This node cannot be changed unless override is enabled.
              {overrideDescription ? ` ${overrideDescription}` : ""}
            </p>
          )}
          {listEditor ? (
            <div className="thinking-monologue-row-list nodrag nowheel flex min-h-0 flex-1 flex-col gap-2">
              <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto pr-1">
                {listRows.map((line, index) => (
                  <div key={index} className="thinking-monologue-row flex items-center gap-2">
                    <span className="w-6 shrink-0 text-right font-mono text-[8px] text-foreground/25">
                      {(index + 1).toString().padStart(2, "0")}
                    </span>
                    <input
                      ref={(element) => {
                        listInputRefs.current[index] = element;
                      }}
                      value={line}
                      readOnly={!canEdit}
                      disabled={loading || saving}
                      onChange={(event) => updateListRow(index, event.target.value)}
                      onKeyDown={(event) => handleListKeyDown(event, index)}
                      onPaste={(event) => handleListPaste(event, index)}
                      aria-label={`Thinking monologue line ${index + 1}`}
                      placeholder="visible waiting line"
                      className="w-full min-w-0 bg-foreground/[0.04] border border-foreground/[0.08] px-3 py-2 font-mono text-[11px] text-foreground/75 outline-none transition-colors placeholder:text-foreground/[0.18] focus:border-foreground/20 disabled:opacity-40"
                    />
                    <button
                      type="button"
                      aria-label="Remove thinking monologue line"
                      disabled={listControlsDisabled}
                      onClick={() => removeListRow(index)}
                      className="flex h-8 w-8 shrink-0 items-center justify-center border border-foreground/[0.08] bg-foreground/[0.03] text-foreground/35 transition-colors hover:border-foreground/18 hover:text-foreground/65 disabled:pointer-events-none disabled:opacity-30"
                    >
                      <XIcon size="sm" />
                    </button>
                  </div>
                ))}
              </div>
              <button
                type="button"
                aria-label="Add thinking monologue line"
                disabled={listControlsDisabled}
                onClick={() => addListRow()}
                className="flex h-8 items-center justify-center gap-2 border border-accent/20 bg-accent/[0.04] font-mono text-[8px] tracking-[0.18em] uppercase text-accent/60 transition-colors hover:border-accent/40 hover:text-accent/80 disabled:pointer-events-none disabled:opacity-30"
              >
                <PlusIcon size="sm" />
                Add line
              </button>
            </div>
          ) : (
            <textarea
              value={draft}
              rows={isLarge ? undefined : (inputRows ?? 6)}
              readOnly={readOnly || lockedByOverride}
              disabled={loading || saving}
              onChange={(event) => onChange(event.target.value)}
              placeholder="..."
              className={taClass}
            />
          )}
        </div>
      </NodeShell>

      <Handle
        type="source"
        position={Position.Right}
        style={{ ...AT_RIGHT, width: 14, height: 14, background: "var(--accent)", borderRadius: "50%", border: "3px solid var(--background)", zIndex: 9999, animation: "handle-pulse 2.4s ease-out infinite" }}
      />
    </div>
  );
}
