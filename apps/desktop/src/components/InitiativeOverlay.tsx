import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { usePendingInitiatives } from "../hooks/usePendingInitiatives";

/**
 * Surfaces the oldest pending IL3 initiative as a corner card. Rendered
 * only when something is pending — the server never creates rows unless
 * the user opted in (`initiativeEnabled`).
 */
export default function InitiativeOverlay() {
  const { user } = useAuth();
  const { pending, ack } = usePendingInitiatives(user?.id);
  const navigate = useNavigate();
  const current = pending[0];
  if (!current) return null;

  return (
    <div className="pointer-events-auto fixed bottom-6 right-6 z-40 w-80 border border-primary/70 bg-card/95 p-4 backdrop-blur">
      <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground/60">
        {current.drive.replace(/_/g, " ")}
      </p>
      <p className="mt-2 text-sm leading-relaxed text-foreground">
        {current.text}
      </p>
      <div className="mt-4 flex items-center justify-between gap-3">
        <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground/40">
          {pending.length > 1 ? `+${pending.length - 1} more` : ""}
        </span>
        <div className="flex gap-2">
          <Link
            to="/chat"
            onClick={(event) => {
              // Each route renders its own Layout, so navigating remounts
              // the poller with a fresh tombstone set. Ack must complete
              // before navigation or the new poller's initial GET could
              // re-fetch this row while the POST is still in flight.
              event.preventDefault();
              void ack(current.id).then(() => navigate("/chat"));
            }}
            className="border border-primary bg-input px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-foreground transition-colors hover:bg-background"
          >
            Reply
          </Link>
          <button
            type="button"
            onClick={() => void ack(current.id)}
            className="border border-border px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.16em] text-muted-foreground transition-colors hover:text-foreground"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}
