const STATUS_STYLES: Record<string, string> = {
  running: "text-success",
  connected: "text-success",
  stopped: "text-muted-foreground/40",
  disabled: "text-muted-foreground/40",
  error: "text-destructive",
};

export default function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.stopped;
  return (
    <span className={`font-mono text-[8px] tracking-widest uppercase ${style}`}>
      {status}
    </span>
  );
}
