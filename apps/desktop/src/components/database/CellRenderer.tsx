import { Icons } from "./Icons";
import { isEncryptedValue } from "./utils";

interface CellRendererProps {
  value: unknown;
  rowIdx?: number;
  col?: string;
  isEditing?: boolean;
  editValue?: string;
  onEditChange?: (value: string) => void;
  isExpanded: boolean;
  onToggleExpand: () => void;
  onCopy: () => void;
  copied: boolean;
}

export function CellRenderer({
  value,
  isEditing,
  editValue,
  onEditChange,
  isExpanded,
  onToggleExpand,
  onCopy,
  copied,
}: CellRendererProps) {
  // Editing mode
  if (isEditing) {
    return (
      <input
        type="text"
        value={editValue ?? ""}
        onChange={(e) => onEditChange?.(e.target.value)}
        className="w-full min-w-[60px] bg-input border border-border rounded px-1.5 py-0.5 text-[12px] outline-none focus:border-primary/40"
        autoFocus
      />
    );
  }

  // Null/undefined/empty handling
  if (value === null)
    return (
      <span className="text-muted-foreground/40 italic text-[11px]">NULL</span>
    );
  if (value === undefined)
    return (
      <span className="text-muted-foreground/40 italic text-[11px]">undefined</span>
    );
  if (value === "")
    return <span className="text-muted-foreground/30 text-[11px]">(empty)</span>;

  const str = String(value);

  // ENCRYPTED DATA - Show only lock icon, not raw value
  if (isEncryptedValue(value)) {
    return (
      <div className="flex items-center gap-1.5">
        <span
          className="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500 border border-amber-500/30 cursor-help"
          title="Encrypted data - decryption key not available"
        >
          <Icons.Lock />
          <span>encrypted</span>
        </span>
      </div>
    );
  }

  // JSON handling
  const isJson = str.startsWith("{") || str.startsWith("[");
  if (isJson) {
    try {
      const parsed = JSON.parse(str);
      const formatted = JSON.stringify(parsed, null, 2);
      return (
        <div className="relative">
          <button
            onClick={onToggleExpand}
            className="text-left w-full hover:text-primary transition-colors"
          >
            <span className="text-primary/60 text-[10px] mr-1">
              {isExpanded ? "▼" : "▶"} JSON
            </span>
            {isExpanded ? (
              <pre className="mt-1 text-[10px] text-muted-foreground/80 whitespace-pre-wrap break-all font-mono">
                {formatted}
              </pre>
            ) : (
              <span className="truncate">{str.slice(0, 60)}…</span>
            )}
          </button>
          <CopyButton onCopy={onCopy} copied={copied} />
        </div>
      );
    } catch {
      // Not valid JSON, fall through
    }
  }

  // Long text handling
  const isLong = str.length > 80;
  if (isLong && !isExpanded) {
    return (
      <div className="flex items-center gap-1 group/cell">
        <button
          onClick={onToggleExpand}
          className="text-left hover:text-primary transition-colors truncate"
        >
          {str.slice(0, 80)}…
          <span className="text-muted-foreground/50 text-[10px] ml-1">
            [+{str.length - 80}]
          </span>
        </button>
        <CopyButton onCopy={onCopy} copied={copied} />
      </div>
    );
  }

  if (isExpanded) {
    return (
      <div className="whitespace-pre-wrap break-all">
        {str}
        <button
          onClick={onToggleExpand}
          className="text-muted-foreground/50 text-[10px] ml-2 hover:text-primary"
        >
          collapse
        </button>
        <CopyButton onCopy={onCopy} copied={copied} />
      </div>
    );
  }

  // Default display
  return (
    <div className="flex items-center gap-1 group/cell">
      <span className="truncate">{str}</span>
      <CopyButton onCopy={onCopy} copied={copied} />
    </div>
  );
}

function CopyButton({
  onCopy,
  copied,
}: {
  onCopy: () => void;
  copied: boolean;
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onCopy();
      }}
      className="opacity-0 group-hover/cell:opacity-100 p-0.5 text-muted-foreground/50 hover:text-primary transition-opacity"
      title="Copy"
    >
      {copied ? <Icons.Check /> : <Icons.Copy />}
    </button>
  );
}
