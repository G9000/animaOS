import { useCallback, useEffect, useRef, useState } from "react";
import { Handle, Position, useUpdateNodeInternals, type NodeProps } from "@xyflow/react";
import { NodeShell, type NodeAction } from "@anima/standard-templates";
import type { AvatarNode } from "./types";
import { UserIcon } from "@anima/standard-templates";

const AT_RIGHT: React.CSSProperties = {
  position: "absolute",
  right: 0,
  top: "50%",
  transform: "translate(50%, -50%)",
  pointerEvents: "none",
};

// Matches w-56 / h-48 card dimensions
const FW = 224;
const FH = 192;

export function AgentAvatarNode({ data, id }: NodeProps<AvatarNode>) {
  const updateNodeInternals = useUpdateNodeInternals();
  useEffect(() => { updateNodeInternals(id); }, [id, updateNodeInternals]);

  const { avatarUrl, agentName, uploading, hasCustomAvatar, onUploadClick, onRemoveAvatar, onCropSave, onClose } = data;

  const [adjusting, setAdjusting]   = useState(false);
  const [dragging, setDragging]     = useState(false);
  const [scale, setScale]           = useState(1);
  const [offset, setOffset]         = useState({ x: 0, y: 0 });
  const [natSize, setNatSize]       = useState({ w: FW, h: FH });

  const dragRef = useRef<{ px: number; py: number; ox: number; oy: number } | null>(null);
  const imgRef  = useRef<HTMLImageElement>(null);

  const baseScale = Math.max(FW / natSize.w, FH / natSize.h);

  const clamp = useCallback((ox: number, oy: number, s: number) => {
    const rw = natSize.w * baseScale * s;
    const rh = natSize.h * baseScale * s;
    const mx = Math.max(0, (rw - FW) / 2);
    const my = Math.max(0, (rh - FH) / 2);
    return { x: Math.max(-mx, Math.min(mx, ox)), y: Math.max(-my, Math.min(my, oy)) };
  }, [natSize, baseScale]);

  const onScaleChange = (s: number) => {
    setScale(s);
    setOffset(o => clamp(o.x, o.y, s));
  };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    setDragging(true);
    dragRef.current = { px: e.clientX, py: e.clientY, ox: offset.x, oy: offset.y };
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!dragRef.current) return;
    e.stopPropagation();
    const dx = e.clientX - dragRef.current.px;
    const dy = e.clientY - dragRef.current.py;
    setOffset(clamp(dragRef.current.ox + dx, dragRef.current.oy + dy, scale));
  };

  const onPointerUp = (e: React.PointerEvent) => {
    e.stopPropagation();
    dragRef.current = null;
    setDragging(false);
  };

  const handleApply = useCallback(() => {
    const img = imgRef.current;
    if (!img) return;

    const totalScale = baseScale * scale;
    const rw = natSize.w * totalScale;
    const rh = natSize.h * totalScale;
    const imgLeft = (FW - rw) / 2 + offset.x;
    const imgTop  = (FH - rh) / 2 + offset.y;

    // Source rect in natural image coordinates
    const sx = -imgLeft / totalScale;
    const sy = -imgTop  / totalScale;
    const sw =  FW / totalScale;
    const sh =  FH / totalScale;

    const canvas = document.createElement("canvas");
    canvas.width  = FW * 2;
    canvas.height = FH * 2;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, FW * 2, FH * 2);
    canvas.toBlob(blob => {
      if (!blob) return;
      onCropSave(new File([blob], "avatar.jpg", { type: "image/jpeg" }));
      setAdjusting(false);
    }, "image/jpeg", 0.92);
  }, [baseScale, scale, natSize, offset, onCropSave]);

  const enterAdjust = () => { setScale(1); setOffset({ x: 0, y: 0 }); setAdjusting(true); };
  const cancelAdjust = () => { setAdjusting(false); };

  const actions: NodeAction[] = adjusting
    ? [
        { id: "apply",  label: "Apply",  onClick: handleApply },
        { id: "cancel", label: "Cancel", onClick: cancelAdjust },
      ]
    : [
        { id: "upload", label: uploading ? "Uploading…" : "Upload", onClick: onUploadClick },
        ...(hasCustomAvatar ? [{ id: "adjust", label: "Adjust", onClick: enterAdjust }] : []),
        ...(hasCustomAvatar ? [{ id: "remove", label: "Remove", onClick: onRemoveAvatar }] : []),
      ];

  // Computed image rect for adjust mode
  const rw = natSize.w * baseScale * scale;
  const rh = natSize.h * baseScale * scale;
  const imgLeft = (FW - rw) / 2 + offset.x;
  const imgTop  = (FH - rh) / 2 + offset.y;

  return (
    <div style={{ position: "relative" }}>
      <NodeShell
        title={agentName || "Anima"}
        icon={<UserIcon size="sm" className="text-foreground/25" />}
        onClose={onClose}
        actions={actions}
        required
        media={
          adjusting ? (
            <div
              className="nodrag relative overflow-hidden"
              style={{ height: FH, cursor: dragging ? "grabbing" : "grab" }}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerLeave={onPointerUp}
            >
              <img
                ref={imgRef}
                src={avatarUrl}
                alt=""
                crossOrigin="anonymous"
                onLoad={e => setNatSize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
                style={{
                  position: "absolute",
                  width: rw, height: rh,
                  left: imgLeft, top: imgTop,
                  userSelect: "none", pointerEvents: "none",
                }}
                draggable={false}
              />
              {/* Rule-of-thirds grid */}
              <div
                className="absolute inset-0 pointer-events-none"
                style={{
                  backgroundImage: [
                    "linear-gradient(to right, rgba(255,255,255,0.07) 1px, transparent 1px)",
                    "linear-gradient(to bottom, rgba(255,255,255,0.07) 1px, transparent 1px)",
                  ].join(", "),
                  backgroundSize: "33.33% 100%, 100% 33.33%",
                }}
              />
              {/* Center crosshair */}
              <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                <div style={{ width: 1, height: 24, background: "rgba(255,255,255,0.25)", position: "absolute" }} />
                <div style={{ width: 24, height: 1, background: "rgba(255,255,255,0.25)", position: "absolute" }} />
              </div>
            </div>
          ) : (
            <div
              className="group/av relative overflow-hidden cursor-pointer"
              style={{ height: FH }}
              onClick={onUploadClick}
            >
              <img
                src={avatarUrl}
                alt={agentName}
                className="w-full h-full object-cover transition-transform duration-500 group-hover/av:scale-105"
              />
              <div
                className="absolute inset-0 flex items-center justify-center opacity-0 group-hover/av:opacity-100 transition-all duration-200"
                style={{ background: "color-mix(in oklch, var(--background) 35%, transparent)" }}
              >
                <span className="font-mono text-[8px] tracking-[0.18em] uppercase text-foreground/80">
                  {uploading ? "Uploading…" : "Change photo"}
                </span>
              </div>
            </div>
          )
        }
        className="w-56"
      >
        {adjusting ? (
          <div className="px-3.5 py-3 space-y-2.5">
            <div className="flex items-center gap-2.5">
              <span className="font-mono text-[9px] text-muted-foreground/40 select-none">−</span>
              <input
                type="range"
                min={100}
                max={300}
                step={5}
                value={Math.round(scale * 100)}
                onChange={e => onScaleChange(Number(e.target.value) / 100)}
                className="nodrag flex-1 h-px"
                style={{ accentColor: "var(--accent)" }}
              />
              <span className="font-mono text-[9px] text-muted-foreground/40 select-none">+</span>
            </div>
            <p className="font-mono text-[8px] text-center text-muted-foreground/25 tracking-wide">
              drag to reposition
            </p>
          </div>
        ) : (
          <div className="h-1" />
        )}
      </NodeShell>

      <Handle
        type="source"
        position={Position.Right}
        style={{ ...AT_RIGHT, width: 14, height: 14, background: "var(--accent)", borderRadius: "50%", border: "3px solid var(--background)", zIndex: 9999, animation: "handle-pulse 2.4s ease-out infinite" }}
      />
    </div>
  );
}
