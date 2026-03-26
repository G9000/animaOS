import { useWaitlist } from "../lib/useWaitlist";

export default function EmailSubscribe() {
  const { email, setEmail, state, submit } = useWaitlist();

  if (state === "done") {
    return (
      <div className="my-12 border border-border bg-card px-6 py-5">
        <p className="font-mono text-[9px] tracking-[0.25em] uppercase text-muted-foreground/40 mb-1">
          // confirmed
        </p>
        <p className="font-sans text-sm text-foreground">
          You're on the list. I'll find you when I wake up.
        </p>
      </div>
    );
  }

  return (
    <div className="my-12 border border-border bg-card px-6 py-8">
      <p className="font-mono text-[9px] tracking-[0.3em] uppercase text-muted-foreground/40 mb-4">
        // follow the build
      </p>
      <p className="font-sans text-sm text-muted-foreground leading-relaxed mb-6 max-w-sm">
        I write when something worth saying happens. No schedule. No noise. Just dispatches from the build.
      </p>
      <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row sm:gap-px">
        <div className="flex items-center border border-border bg-background px-4 py-3 flex-1 gap-2">
          <span className="font-mono text-sm text-muted-foreground/30 shrink-0">&gt;</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="your@email.com"
            required
            className="flex-1 bg-transparent font-mono text-sm text-foreground placeholder:text-muted-foreground/30 focus:outline-none"
          />
        </div>
        <button
          type="submit"
          disabled={state === "loading"}
          className="relative overflow-hidden border border-border bg-card px-6 py-3 font-mono text-[10px] tracking-[0.2em] uppercase text-muted-foreground w-full sm:w-auto
            before:absolute before:inset-0 before:bg-foreground before:-translate-x-full hover:before:translate-x-0 before:transition-transform before:duration-500 before:ease-[cubic-bezier(0.16,1,0.3,1)]
            hover:text-background transition-colors disabled:opacity-50"
        >
          <span className="relative z-10">
            {state === "loading" ? "..." : "notify me"}
          </span>
        </button>
      </form>
    </div>
  );
}
