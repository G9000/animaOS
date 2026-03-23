const STATUS_STYLES: Record<string, string> = {
  running: "text-success",
  connected: "text-success",
  stopped: "text-text-muted/40",
  disabled: "text-text-muted/40",
  error: "text-danger",
};

export default function StatusBadge({ status }: { status: string }) {
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.stopped;
  return (
    <span className={`font-mono text-[8px] tracking-widest uppercase ${style}`}>
      {status}
    </span>
  );
}
