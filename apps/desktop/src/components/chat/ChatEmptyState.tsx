export function ChatEmptyState() {
  return (
    <div className="flex items-center justify-center h-full min-h-[40vh]">
      <div className="text-center space-y-4">
        <div className="font-mono text-[10px] text-primary/40 tracking-[0.5em]">
          //READY
        </div>
        <div className="w-8 h-px bg-primary/20 mx-auto" />
        <p className="font-mono text-muted-foreground/50 text-[10px] tracking-wider">
          AWAITING INPUT
        </p>
      </div>
    </div>
  );
}
