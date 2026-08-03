import { useState, useRef, useEffect } from "react";
import { cn } from "../utils/cn";
import { PlusIcon, ImageIcon, FileIcon, DocumentIcon } from "../icons";

const ITEMS = [
  { label: "Image",    type: "image",    icon: ImageIcon },
  { label: "File",     type: "file",     icon: FileIcon },
  { label: "Document", type: "document", icon: DocumentIcon },
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
      {/* Trigger */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="Attach"
        className={cn(
          "size-7 flex items-center justify-center border transition-colors duration-150",
          open
            ? "border-accent bg-accent text-accent-foreground"
            : "border-hairline text-foreground/35 hover:border-hairline-strong hover:text-foreground/65 hover:bg-foreground/[0.04]",
        )}
      >
        <PlusIcon size="sm" />
      </button>

      {/* Popup */}
      {open && (
        <div className="absolute bottom-full left-0 mb-3 flex flex-col z-50 overflow-hidden shadow-[0_20px_50px_-12px_rgba(0,0,0,0.4)]">
          {/* Items */}
          <div className="flex bg-accent/90 backdrop-blur-[40px] border border-accent/30">
            {ITEMS.map(({ label, type, icon: Icon }, i) => (
              <button
                key={type}
                type="button"
                onClick={() => { onAttach?.(type); setOpen(false); }}
                className={cn(
                  "flex flex-col items-center gap-1.5 px-4 py-3 min-w-[64px]",
                  "text-accent-foreground/70 hover:text-accent-foreground hover:bg-black/10",
                  "transition-colors",
                  i > 0 && "border-l border-accent-foreground/10",
                )}
              >
                <Icon size="sm" />
                <span className="font-mono text-micro tracking-caps-3 uppercase">
                  {label}
                </span>
              </button>
            ))}
          </div>
          {/* Arrow pointing down to the trigger */}
          <div className="w-3 h-1.5 self-start ml-2.5 overflow-hidden">
            <div className="w-3 h-3 bg-accent/90 border border-accent/30 rotate-45 origin-top-left translate-x-0 translate-y-[-50%]" />
          </div>
        </div>
      )}
    </div>
  );
}
