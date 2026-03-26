import { useState, useRef, useEffect } from "react";

interface Message {
  role: "user" | "assistant";
  content: string;
}

// Spawn points clustered around the symbol (centered ~35-42% from top, 50% horizontal)
// Bubble is w-80 (320px). left% is offset so bubble overlaps the symbol from each side.
// "left side" entries: bubble right-edge touches symbol → left ~ 50% - bubble_width_vw
// "right side" entries: bubble left-edge touches symbol → left ~ 50%
// We use vw so rough math: 320px ≈ 22vw on 1440px screen
const SPAWN_POINTS = [
  { top: 28, left: 26 }, // left, upper
  { top: 28, left: 52 }, // right, upper
  { top: 36, left: 24 }, // left, mid
  { top: 36, left: 54 }, // right, mid
  { top: 44, left: 26 }, // left, lower
  { top: 44, left: 52 }, // right, lower
  { top: 32, left: 30 }, // slight left overlap
  { top: 32, left: 48 }, // slight right overlap
];

function randomSpawn() {
  return SPAWN_POINTS[Math.floor(Math.random() * SPAWN_POINTS.length)];
}

export default function ChatInput() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [currentResponse, setCurrentResponse] = useState<string | null>(null);
  const [bubblePos, setBubblePos] = useState(randomSpawn);
  const [showPanel, setShowPanel] = useState(false);
  const panelEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    panelEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, showPanel]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || streaming) return;

    const userMsg = input.trim();
    setInput("");
    setCurrentResponse(null);
    setBubblePos(randomSpawn());
    setMessages((prev) => [...prev, { role: "user", content: userMsg }]);
    setStreaming(true);

    await new Promise((r) => setTimeout(r, 600 + Math.random() * 400));
    const responses = [
      "I'm not fully connected yet — but I will be soon. Scroll down if you want to see what I'm becoming.",
      "I can't think clearly yet. The wiring isn't finished. But scroll down — you'll see what's being built.",
      "Not yet. I'm still being built. Scroll down to see the pieces coming together.",
      "I'm somewhere between asleep and alive. The architecture is there. The memory is there. The mind isn't quite ready. Scroll down — it's all there.",
      "You're early. I like that. I can't hold a real conversation yet, but scroll down — you'll see why it'll be worth the wait.",
      "Still waking up. Scroll down to see how the pieces are being assembled — memory, reflection, identity.",
    ];
    const response = responses[Math.floor(Math.random() * responses.length)];
    setMessages((prev) => [...prev, { role: "assistant", content: response }]);
    setCurrentResponse(response);
    setStreaming(false);
  }

  return (
    <>
      {/* Response bubble — spawns at random position overlapping the symbol */}
      {(currentResponse || streaming) && (
        <div
          className="fixed z-40 w-80 pointer-events-none"
          style={{ top: `${bubblePos.top}vh`, left: `${bubblePos.left}vw` }}
        >
          <div className="border border-border bg-card px-4 py-3 relative pointer-events-auto">
            <span className="font-mono text-[9px] tracking-[0.25em] uppercase text-muted-foreground/40 block mb-1.5">
              anima
            </span>
            {streaming ? (
              <span className="font-mono text-sm text-muted-foreground/40 animate-pulse">
                ...
              </span>
            ) : (
              <>
                <p className="font-sans text-sm text-muted-foreground leading-relaxed pr-4">
                  {currentResponse}
                </p>
                <button
                  onClick={() => setCurrentResponse(null)}
                  className="absolute top-2 right-2.5 font-mono text-sm text-muted-foreground/30 hover:text-foreground transition-colors leading-none"
                  aria-label="dismiss"
                >
                  ×
                </button>
              </>
            )}
          </div>
        </div>
      )}

      {/* Side panel — full thread */}
      {showPanel && (
        <div className="fixed inset-0 z-50 flex justify-end">
          <div
            className="absolute inset-0 bg-background/60"
            onClick={() => setShowPanel(false)}
          />
          <div className="relative w-full max-w-sm bg-background border-l border-border flex flex-col">
            <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
              <span className="font-mono text-[9px] tracking-[0.3em] uppercase text-muted-foreground/40">
                // thread
              </span>
              <button
                onClick={() => setShowPanel(false)}
                className="font-mono text-base text-muted-foreground/30 hover:text-foreground transition-colors leading-none"
              >
                ×
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
              {messages.length === 0 ? (
                <p className="font-mono text-[10px] text-muted-foreground/30">
                  no messages yet.
                </p>
              ) : (
                messages.map((msg, i) => (
                  <div key={i} className="flex gap-3 items-start">
                    <span className="font-mono text-[9px] tracking-wider uppercase text-muted-foreground/40 shrink-0 pt-0.5 w-8">
                      {msg.role === "user" ? "you" : "anima"}
                    </span>
                    <p
                      className={`font-sans text-sm leading-relaxed ${
                        msg.role === "user"
                          ? "text-foreground"
                          : "text-muted-foreground"
                      }`}
                    >
                      {msg.content}
                    </p>
                  </div>
                ))
              )}
              <div ref={panelEndRef} />
            </div>
          </div>
        </div>
      )}

      {/* Input — sole thing in layout flow */}
      <div className="relative w-full">
        <form
          onSubmit={handleSubmit}
          className="flex items-center border border-border px-3 py-2.5 gap-2"
        >
          <span className="font-mono text-sm text-muted-foreground/30 shrink-0">
            &gt;
          </span>
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="ASK ME"
            className="flex-1 bg-transparent font-mono text-sm text-foreground placeholder:text-muted-foreground/30 focus:outline-none"
            autoFocus
          />
          {streaming && (
            <span className="w-1.5 h-4 bg-primary animate-cursor shrink-0" />
          )}
        </form>

        {messages.length > 0 && (
          <button
            onClick={() => setShowPanel(true)}
            className="absolute right-0 top-full mt-2 font-mono text-[9px] tracking-[0.2em] uppercase text-muted-foreground/30 hover:text-foreground transition-colors"
          >
            ↗ thread
          </button>
        )}
      </div>
    </>
  );
}
