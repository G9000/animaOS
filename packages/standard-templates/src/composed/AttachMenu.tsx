import { useState, useRef, useEffect } from "react";
import { Button } from "../primitives/Button";
import { PlusIcon, ImageIcon, FileIcon, DocumentIcon } from "../icons";

const ITEMS = [
  { label: "image",    icon: <ImageIcon className="w-3.5 h-3.5" /> },
  { label: "file",     icon: <FileIcon className="w-3.5 h-3.5" /> },
  { label: "document", icon: <DocumentIcon className="w-3.5 h-3.5" /> },
];

export interface AttachMenuProps {
  onAttach?: (type: string) => void;
}

export function AttachMenu({ onAttach }: AttachMenuProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div ref={ref} className="relative shrink-0">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        iconOnly
        icon={<PlusIcon />}
        onClick={() => setOpen((v) => !v)}
      />

      {open && (
        <div className="absolute bottom-full left-0 mb-2 w-40 border border-foreground/10 bg-foreground/10 flex flex-col gap-px animate-fade-in z-50">
          {ITEMS.map(({ label, icon }) => (
            <button
              key={label}
              type="button"
              onClick={() => { onAttach?.(label); setOpen(false); }}
              className="group relative overflow-hidden flex items-center gap-3 px-3 py-2 bg-background font-mono text-detail text-subtle-foreground before:absolute before:inset-0 before:-translate-x-full before:bg-foreground before:transition-transform before:duration-300 before:ease-[cubic-bezier(0.16,1,0.3,1)] hover:before:translate-x-0"
            >
              <span className="relative z-10 flex items-center gap-3 group-hover:text-background transition-colors duration-300">
                {icon}
                {label}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
