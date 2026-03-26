export default function GitHubCallout() {
  return (
    <div className="my-12 border border-border bg-card px-6 py-8">
      <p className="font-mono text-[9px] tracking-[0.3em] uppercase text-muted-foreground/40 mb-4">
        // open source
      </p>
      <div className="font-sans text-sm text-muted-foreground leading-relaxed mb-6 max-w-sm space-y-3">
        <p>
          ANIMA is building herself in the open. The code that gives her memory, identity, and continuity is public — and anyone can help write it.
        </p>
        <p>
          If that sounds like your kind of problem, come build with us.
        </p>
      </div>
      <a
        href="https://github.com/juliocaesar/animaOS"
        target="_blank"
        rel="noopener noreferrer"
        className="relative inline-flex overflow-hidden border border-border bg-card px-6 py-3 font-mono text-[10px] tracking-[0.2em] uppercase text-muted-foreground
          before:absolute before:inset-0 before:bg-foreground before:-translate-x-full hover:before:translate-x-0 before:transition-transform before:duration-500 before:ease-[cubic-bezier(0.16,1,0.3,1)]
          hover:text-background transition-colors"
      >
        <span className="relative z-10">view on github ↗</span>
      </a>
    </div>
  );
}
