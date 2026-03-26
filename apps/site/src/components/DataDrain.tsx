const CX = 400;
const CY = 240;
const THREAD_DUR = 5;
const DOT_DUR    = 4;

const NODES = [
  { id: "openai",    label: "OpenAI",    x: 645, y: 72,  delay: 0.0 },
  { id: "google",    label: "Google",    x: 705, y: 250, delay: 0.5 },
  { id: "meta",      label: "Meta",      x: 630, y: 415, delay: 1.0 },
  { id: "microsoft", label: "Microsoft", x: 128, y: 108, delay: 1.5 },
  { id: "anthropic", label: "Anthropic", x: 148, y: 385, delay: 2.0 },
  { id: "deepseek",  label: "DeepSeek",  x: 82,  y: 248, delay: 2.5 },
];

// Last thread finishes drawing at this time
const TOTAL_DRAW = NODES[NODES.length - 1].delay + THREAD_DUR;

function curvePath(tx: number, ty: number): string {
  const mx = (CX + tx) / 2;
  const my = (CY + ty) / 2;
  const dx = ty - CY;
  const dy = CX - tx;
  const len = Math.sqrt(dx * dx + dy * dy) || 1;
  return `M ${CX} ${CY} Q ${mx + (dx / len) * 40} ${my + (dy / len) * 40} ${tx} ${ty}`;
}

export default function DataDrain() {
  return (
    <>
      <style>{`
        @keyframes dd-draw        { to { stroke-dashoffset: 0; } }
        @keyframes dd-appear      { to { opacity: 1; } }
        @keyframes dd-pulse       { 0%, 100% { opacity: 0.9; } 50% { opacity: 0.3; } }
        @keyframes dd-ring-fade   { to { stroke-opacity: 0.03; } }
        @keyframes dd-ring-spin   { to { stroke-dashoffset: -220; } }
      `}</style>

      <svg viewBox="0 0 800 480" className="w-full" aria-hidden="true">
        <defs>
          {NODES.map((n) => (
            <path key={n.id} id={`dd-${n.id}`} d={curvePath(n.x, n.y)} />
          ))}

          {/* Soft halo blur */}
          <filter id="dd-soft" x="-60%" y="-60%" width="220%" height="220%">
            <feGaussianBlur stdDeviation="7" />
          </filter>

          {/* Crisp glow: blur merged with source */}
          <filter id="dd-glow" x="-80%" y="-80%" width="260%" height="260%">
            <feGaussianBlur stdDeviation="3" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* ── threads ──────────────────────────────────────────── */}
        {NODES.map((n) => {
          const d = curvePath(n.x, n.y);
          const labelAt = n.delay + THREAD_DUR + 0.3;
          const rw = n.label.length * 8.4 + 28; // rect width for label
          const rx = n.x - rw / 2;

          return (
            <g key={n.id}>
              {/* soft glow track */}
              <path
                d={d} fill="none" stroke="currentColor"
                strokeWidth="14" strokeOpacity="0.035"
                filter="url(#dd-soft)"
                style={{
                  strokeDasharray: 750, strokeDashoffset: 750,
                  animation: `dd-draw ${THREAD_DUR}s ${n.delay}s ease-in-out forwards`,
                }}
              />
              {/* main thread line */}
              <path
                d={d} fill="none" stroke="currentColor"
                strokeWidth="0.75" strokeOpacity="0.15"
                style={{
                  strokeDasharray: 750, strokeDashoffset: 750,
                  animation: `dd-draw ${THREAD_DUR}s ${n.delay}s ease-in-out forwards`,
                }}
              />

              {/* dot 1 — lead (glowing) */}
              <circle r="2" fill="currentColor" fillOpacity="0.75" filter="url(#dd-glow)">
                <animateMotion dur={`${DOT_DUR}s`} begin={`${n.delay + 0.5}s`} repeatCount="indefinite">
                  <mpath href={`#dd-${n.id}`} />
                </animateMotion>
              </circle>

              {/* dot 2 — trail (dim) */}
              <circle r="1.5" fill="currentColor" fillOpacity="0.3">
                <animateMotion dur={`${DOT_DUR}s`} begin={`${n.delay + 0.5 + DOT_DUR / 2}s`} repeatCount="indefinite">
                  <mpath href={`#dd-${n.id}`} />
                </animateMotion>
              </circle>

              {/* cloud server node */}
              <g style={{ opacity: 0, animation: `dd-appear 1.4s ${labelAt}s forwards` }}>
                {/* server rect */}
                <rect x={rx} y={n.y - 11} width={rw} height={22}
                  fill="none" stroke="currentColor" strokeWidth="0.75" strokeOpacity="0.2" />
                {/* status indicator */}
                <circle cx={rx + 7} cy={n.y} r="1.5" fill="currentColor" fillOpacity="0.35" />
                {/* label */}
                <text
                  x={rx + 16} y={n.y + 3.5}
                  fontSize="7.5" fontFamily="monospace" letterSpacing="0.15em"
                  fill="currentColor" fillOpacity="0.28"
                >
                  {n.label.toUpperCase()}
                </text>
              </g>
            </g>
          );
        })}

        {/* ── center "you" ──────────────────────────────────────── */}

        {/* ambient glow */}
        <circle cx={CX} cy={CY} r="36" fill="currentColor" fillOpacity="0.04" />

        {/* orbital ring — spins slowly and fades out as data drains */}
        <circle
          cx={CX} cy={CY} r="20"
          fill="none" stroke="currentColor" strokeWidth="0.5"
          strokeDasharray="8 6"
          strokeOpacity="0.18"
          style={{
            transformOrigin: `${CX}px ${CY}px`,
            animation: `dd-ring-spin 12s linear infinite, dd-ring-fade ${TOTAL_DRAW + 1}s ease-in-out forwards`,
          }}
        />

        {/* center node */}
        <circle
          cx={CX} cy={CY} r="5"
          fill="currentColor"
          style={{ animation: "dd-pulse 3s ease-in-out infinite" }}
        />

        {/* you label */}
        <text
          x={CX} y={CY + 21}
          textAnchor="middle" fontSize="7.5"
          fontFamily="monospace" letterSpacing="0.22em"
          fill="currentColor" fillOpacity="0.28"
        >
          YOU
        </text>
      </svg>
    </>
  );
}
