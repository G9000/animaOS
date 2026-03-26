import { useState } from "react";

export default function WaitlistForm() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "submitting" | "success" | "error">("idle");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email) return;
    setStatus("submitting");

    try {
      await new Promise((resolve) => setTimeout(resolve, 800));
      setStatus("success");
      setEmail("");
    } catch {
      setStatus("error");
    }
  }

  if (status === "success") {
    return (
      <p className="font-mono text-[9px] tracking-[0.3em] uppercase text-muted-foreground/60">
        [ registered ] — we'll be in touch
      </p>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row gap-px w-full max-w-sm mx-auto">
      <div className="flex-1 flex items-center bg-input border border-border px-3 py-2.5 gap-2">
        <span className="font-mono text-xs text-muted-foreground/40 shrink-0">&gt;</span>
        <input
          type="email"
          required
          placeholder="you@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="flex-1 bg-transparent font-mono text-xs text-foreground placeholder:text-muted-foreground/40 focus:outline-none tracking-wider"
        />
      </div>
      <button
        type="submit"
        disabled={status === "submitting"}
        className="relative overflow-hidden border border-border px-4 py-2.5 font-mono text-[10px] tracking-[0.2em] uppercase text-muted-foreground hover:text-background transition-colors disabled:opacity-30
          before:absolute before:inset-0 before:bg-text before:-translate-x-full hover:before:translate-x-0 before:transition-transform before:duration-500 before:ease-[cubic-bezier(0.16,1,0.3,1)]"
      >
        <span className="relative z-10">
          {status === "submitting" ? "..." : "Join"}
        </span>
      </button>
      {status === "error" && (
        <p className="font-mono text-[8px] text-destructive/60 mt-1 sm:mt-0 sm:ml-2 self-center">[err] try again</p>
      )}
    </form>
  );
}
