import { useState, useEffect, useRef } from "react";
import { Navigate } from "react-router-dom";
import { api, setUnlockToken } from "../../lib/api";
import { useAuth } from "../../context/AuthContext";

interface PersonaTemplate {
  id: string;
  name: string;
  description: string;
}

interface Line {
  id: number;
  type: "system" | "question" | "user" | "error" | "success" | "divider";
  text: string;
}

const LETTERS: Record<string, number[][]> = {
  A: [
    [0,0,0,1,0,0,0],
    [0,0,1,0,1,0,0],
    [0,1,0,0,0,1,0],
    [0,1,0,0,0,1,0],
    [0,1,1,1,1,1,0],
    [0,1,0,0,0,1,0],
    [0,1,0,0,0,1,0],
  ],
  N: [
    [0,1,0,0,0,1,0],
    [0,1,1,0,0,1,0],
    [0,1,0,1,0,1,0],
    [0,1,0,0,1,1,0],
    [0,1,0,0,0,1,0],
    [0,1,0,0,0,1,0],
    [0,1,0,0,0,1,0],
  ],
  I: [
    [0,0,1,1,1,0,0],
    [0,0,0,1,0,0,0],
    [0,0,0,1,0,0,0],
    [0,0,0,1,0,0,0],
    [0,0,0,1,0,0,0],
    [0,0,0,1,0,0,0],
    [0,0,1,1,1,0,0],
  ],
  M: [
    [0,1,0,0,0,1,0],
    [0,1,1,0,1,1,0],
    [0,1,0,1,0,1,0],
    [0,1,0,0,0,1,0],
    [0,1,0,0,0,1,0],
    [0,1,0,0,0,1,0],
    [0,1,0,0,0,1,0],
  ],
};

function useWaveText(text: string) {
  const [frame, setFrame] = useState("");
  const tickRef = useRef(0);

  useEffect(() => {
    const width = 70;
    const height = 16;
    const chars = " .'-:;=+*#%@";

    const render = () => {
      const tick = tickRef.current;
      const output: string[] = new Array(width * height).fill(" ");
      const zbuffer: number[] = new Array(width * height).fill(-999);

      const points: { x: number; y: number; z: number; char: string }[] = [];
      const letterSpacing = 9;
      const startX = -((text.length * letterSpacing) / 2);

      for (let li = 0; li < text.length; li++) {
        const letter = LETTERS[text[li]];
        if (!letter) continue;

        const letterPhase = tick * 0.05 + li * 0.8;
        const waveY = Math.sin(letterPhase) * 1.5;
        const waveZ = Math.cos(letterPhase) * 2;
        const rot = Math.sin(tick * 0.03 + li * 0.5) * 0.3;

        for (let y = 0; y < letter.length; y++) {
          for (let x = 0; x < letter[y].length; x++) {
            if (letter[y][x]) {
              const baseX = startX + li * letterSpacing + x;
              const baseY = y - 3 + waveY;
              
              const rx = baseX * Math.cos(rot) - baseY * Math.sin(rot);
              const ry = baseX * Math.sin(rot) + baseY * Math.cos(rot);
              const rz = waveZ + Math.sin(tick * 0.1 + x * 0.5) * 0.5;

              points.push({ x: rx, y: ry, z: rz, char: "@" });
              points.push({ x: rx, y: ry, z: rz - 0.5, char: "%" });
            }
          }
        }
      }

      for (const p of points) {
        const ooz = 1 / (5 + p.z * 0.4);
        const xp = Math.floor(width / 2 + p.x * ooz * 2.5);
        const yp = Math.floor(height / 2 - p.y * ooz * 1.8);

        const idx = xp + yp * width;
        if (idx >= 0 && idx < width * height && xp >= 0 && xp < width) {
          if (ooz > zbuffer[idx]) {
            zbuffer[idx] = ooz;
            const lum = Math.floor(ooz * 15 + Math.sin(tick * 0.2) * 2);
            const charIdx = Math.max(0, Math.min(chars.length - 1, lum));
            output[idx] = chars[charIdx];
          }
        }
      }

      let result = "";
      for (let y = 0; y < height; y++) {
        result += output.slice(y * width, (y + 1) * width).join("").replace(/\s+$/, "");
        result += "\n";
      }

      setFrame(result);
      tickRef.current += 1;
    };

    const interval = setInterval(render, 40);
    return () => clearInterval(interval);
  }, [text]);

  return frame;
}

const QUESTIONS = [
  { key: "name", text: "What should I call you?", hint: "Your name" },
  { key: "username", text: "Choose a username.", hint: "Unique identifier" },
  { key: "password", text: "Create a password.", hint: "Min 6 characters" },
  { key: "agent", text: "What is my name?", hint: "Default: Anima" },
  { key: "persona", text: "Choose my personality.", hint: "Select one" },
  { key: "confirm", text: "Create your companion?", hint: "Type yes to confirm" },
];

export default function Register() {
  const { isProvisioned, setUser } = useAuth();
  const [lines, setLines] = useState<Line[]>([]);
  const [input, setInput] = useState("");
  const [step, setStep] = useState(0);
  const [data, setData] = useState({
    name: "",
    username: "",
    password: "",
    agent: "",
    persona: "default",
  });
  const [personas, setPersonas] = useState<PersonaTemplate[]>([]);
  const [done, setDone] = useState(false);
  const [showHint, setShowHint] = useState(true);
  const animaFrame = useWaveText("ANIMA");
  const inputRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const idRef = useRef(0);

  useEffect(() => {
    api.config.personaTemplates().then(setPersonas).catch(() =>
      setPersonas([
        { id: "default", name: "Default", description: "Thoughtful and capable" },
        { id: "companion", name: "Companion", description: "Warm and emotionally attuned" },
      ])
    );
  }, []);

  useEffect(() => {
    const boot = [
      { t: "system" as const, text: "ANIMA OS v0.2.1" },
      { t: "divider" as const, text: "─".repeat(50) },
      { t: "system" as const, text: "Initializing encrypted vault..." },
      { t: "system" as const, text: "Loading neural substrate..." },
      { t: "system" as const, text: "Ready." },
      { t: "divider" as const, text: "─".repeat(50) },
    ];
    let i = 0;
    const t = setInterval(() => {
      if (i < boot.length) {
        addLine(boot[i].t, boot[i].text);
        i++;
      } else {
        clearInterval(t);
        showQuestion(0);
      }
    }, 80);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);

  useEffect(() => {
    inputRef.current?.focus();
  });

  if (isProvisioned) return <Navigate to="/login" replace />;

  const addLine = (type: Line["type"], text: string) => {
    setLines((p) => [...p, { id: idRef.current++, type, text }]);
  };

  const showQuestion = (idx: number) => {
    const q = QUESTIONS[idx];
    addLine("divider", "");
    addLine("question", `┌─ ${q.text}`);
    if (idx === 4 && personas.length > 0) {
      personas.forEach((p, i) => {
        addLine("system", `│  [${i}] ${p.name} ─ ${p.description}`);
      });
    }
    addLine("question", "└─");
  };

  const next = () => {
    const newStep = step + 1;
    setStep(newStep);
    setInput("");
    setShowHint(true);
    if (newStep < QUESTIONS.length) {
      showQuestion(newStep);
    }
  };

  const submit = () => {
    if (!input.trim() || done) return;
    const v = input.trim();
    
    // Echo user input
    const displayValue = step === 2 ? "•".repeat(Math.min(v.length, 20)) : v;
    addLine("user", `   > ${displayValue}`);

    switch (step) {
      case 0:
        if (!v) {
          addLine("error", "   ! Name required");
          return;
        }
        setData((d) => ({ ...d, name: v }));
        addLine("success", `   ✓ Hello, ${v}.`);
        next();
        break;
      case 1:
        if (v.length < 2) {
          addLine("error", "   ! Username too short");
          return;
        }
        setData((d) => ({ ...d, username: v }));
        addLine("success", `   ✓ Username set.`);
        next();
        break;
      case 2:
        if (v.length < 6) {
          addLine("error", "   ! Minimum 6 characters");
          return;
        }
        setData((d) => ({ ...d, password: v }));
        addLine("success", `   ✓ Password secured.`);
        next();
        break;
      case 3:
        const agent = v || "Anima";
        setData((d) => ({ ...d, agent }));
        addLine("success", `   ✓ Agent name: ${agent}.`);
        next();
        break;
      case 4:
        const idx = parseInt(v);
        const selected = !isNaN(idx) && personas[idx] ? personas[idx] : personas[0];
        setData((d) => ({ ...d, persona: selected.id }));
        addLine("success", `   ✓ Personality: ${selected.name}.`);
        next();
        break;
      case 5:
        if (v.toLowerCase() !== "yes") {
          addLine("error", "   ! Cancelled. Type 'yes' to proceed.");
          return;
        }
        addLine("divider", "");
        addLine("system", "Creating your companion...");
        create();
        break;
    }
  };

  const create = async () => {
    try {
      const u = await api.auth.register(
        data.username,
        data.password,
        data.name,
        data.persona as "default" | "companion",
        data.agent || "Anima",
        "",
        "companion"
      );
      setUnlockToken(u.unlockToken);
      setUser({ id: u.id, username: u.username, name: u.name });
      
      addLine("divider", "═".repeat(50));
      addLine("success", "Companion created successfully.");
      addLine("system", "Initializing session...");
      addLine("divider", "─".repeat(50));
      setDone(true);
    } catch (e) {
      addLine("error", `   ! ${e instanceof Error ? e.message : "Error"}`);
    }
  };

  const currentQ = QUESTIONS[step];
  const progress = done ? 100 : Math.round((step / QUESTIONS.length) * 100);

  return (
    <div className="h-screen w-screen bg-black text-white font-mono text-sm flex flex-col">
      {/* Header with animated ANIMA */}
      <div className="p-4 border-b border-white/10">
        <pre className="text-xs whitespace-pre leading-none h-28 opacity-80">{animaFrame}</pre>
      </div>

      {/* Main content area */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: Chat history */}
        <div className="flex-1 overflow-y-auto p-4 space-y-0">
          {lines.map((l) => (
            <div
              key={l.id}
              className={
                l.type === "question"
                  ? "text-white font-bold"
                  : l.type === "user"
                  ? "text-white/80"
                  : l.type === "error"
                  ? "text-white/60"
                  : l.type === "success"
                  ? "text-white/70"
                  : l.type === "divider"
                  ? "text-white/20"
                  : "text-white/50"
              }
            >
              {l.text}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {/* Right: Status panel */}
        <div className="w-48 border-l border-white/10 p-4 hidden lg:block">
          <div className="text-xs text-white/30 mb-2">SETUP PROGRESS</div>
          <div className="h-px bg-white/10 mb-2">
            <div
              className="h-full bg-white/40 transition-all duration-300"
              style={{ width: `${progress}%` }}
            />
          </div>
          <div className="text-right text-xs text-white/30">{progress}%</div>
          
          {step > 0 && (
            <>
              <div className="text-xs text-white/30 mt-6 mb-2">CONFIGURED</div>
              <div className="space-y-1 text-xs">
                {data.name && <div className="text-white/50">Name: {data.name}</div>}
                {data.username && <div className="text-white/50">User: {data.username}</div>}
                {(data.agent || step > 3) && <div className="text-white/50">Agent: {data.agent || "Anima"}</div>}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Input area */}
      <div className="border-t border-white/20 p-4 bg-black">
        {!done ? (
          <div className="max-w-3xl">
            {/* Question label */}
            <div className="flex items-center gap-2 text-xs text-white/40 mb-2">
              <span>STEP {step + 1} OF {QUESTIONS.length}</span>
              <span className="text-white/20">│</span>
              {showHint && currentQ?.hint && (
                <span className="text-white/30">{currentQ.hint}</span>
              )}
            </div>
            
            {/* Input line */}
            <div className="flex items-center gap-3">
              <span className="text-white/60 text-lg">›</span>
              <input
                ref={inputRef}
                type={step === 2 ? "password" : "text"}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  setShowHint(e.target.value.length === 0);
                }}
                onKeyDown={(e) => e.key === "Enter" && submit()}
                className="flex-1 bg-transparent outline-none text-white text-lg"
                spellCheck={false}
                autoComplete="off"
                placeholder={currentQ?.hint}
              />
              {input && (
                <span className="text-xs text-white/30 animate-pulse">
                  press ENTER
                </span>
              )}
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between max-w-3xl">
            <span className="text-white/50">Ready to begin.</span>
            <a 
              href="/" 
              className="px-4 py-2 border border-white/20 hover:border-white/40 hover:bg-white/5 transition-colors"
            >
              ENTER ›
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
