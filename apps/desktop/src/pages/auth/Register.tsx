import { useState, useEffect, useRef } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { Button, StandardInput } from "@anima/standard-templates";
import { api, setUnlockToken } from "../../lib/api";
import { useAuth } from "../../context/AuthContext";

interface PersonaTemplate {
  id: string;
  name: string;
  description: string;
}

const STEPS = [
  { key: "name",     label: "what should i call you?s", password: false },
  { key: "username", label: "choose a username",        password: false },
  { key: "password", label: "create a password",        password: true  },
  { key: "agent",    label: "name your companion",      password: false },
  { key: "persona",  label: "choose a personality",     password: false },
  { key: "confirm",  label: "ready to begin?",          password: false },
] as const;

const glass = "bg-background/20 backdrop-blur-[44px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.24)]";

export default function Register() {
  const { isProvisioned, setUser } = useAuth();
  const navigate = useNavigate();

  const [step, setStep] = useState(0);
  const [data, setData] = useState({ name: "", username: "", password: "", agent: "" });
  const [persona, setPersona] = useState("default");
  const [personas, setPersonas] = useState<PersonaTemplate[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.config.personaTemplates().then(setPersonas).catch(() =>
      setPersonas([
        { id: "default",   name: "Default",   description: "Neutral and practical" },
        { id: "companion", name: "Companion",  description: "Warm and grounded"    },
      ])
    );
  }, []);

  useEffect(() => {
    inputRef.current?.focus();
  }, [step]);

  if (isProvisioned) return <Navigate to="/login" replace />;

  const current     = STEPS[step];
  const isPassword  = current?.password ?? false;
  const isPersona   = step === 4;
  const isConfirm   = step === 5;
  const label       = current?.label ?? "";

  function goBack() {
    setError("");
    if (step === 0) navigate("/init", { replace: true });
    else { setStep(s => s - 1); setInput(""); }
  }

  function advance() {
    setStep(s => s + 1);
    setInput("");
    setError("");
  }

  function submit() {
    if (loading) return;
    setError("");
    const v = input.trim();

    switch (step) {
      case 0:
        if (!v) { setError("name required"); return; }
        setData(d => ({ ...d, name: v }));
        advance();
        break;
      case 1:
        if (v.length < 2) { setError("too short"); return; }
        setData(d => ({ ...d, username: v }));
        advance();
        break;
      case 2:
        if (v.length < 8) { setError("min 8 characters"); return; }
        setData(d => ({ ...d, password: v }));
        advance();
        break;
      case 3:
        setData(d => ({ ...d, agent: v || "Anima" }));
        advance();
        break;
    }
  }

  function selectPersona(id: string) {
    setPersona(id);
    advance();
  }

  async function create() {
    setLoading(true);
    setError("");
    try {
      const u = await api.auth.register(
        data.username,
        data.password,
        data.name,
        persona as "default" | "companion",
        data.agent || "Anima",
        "",
        "companion",
      );
      setUnlockToken(u.unlockToken);
      setUser({ id: u.id, username: u.username, name: u.name });
      navigate("/");
    } catch (e) {
      setError(e instanceof Error ? e.message : "registration failed");
      setLoading(false);
    }
  }

  return (
    <div className="h-screen w-screen text-foreground overflow-hidden relative">
      <div
        className="absolute inset-0 pointer-events-none z-[1]"
        style={{ background: "radial-gradient(ellipse 80% 50% at 50% 100%, rgba(0,0,0,0.88) 0%, transparent 100%)" }}
      />

      <div className="absolute bottom-0 left-0 right-0 z-10 px-8 pb-10 flex items-end justify-between">

        <div className={glass}>
          <Button size="xs" variant="main" onClick={goBack}>← back</Button>
        </div>

        {isPersona ? (
          <div className={glass}>
            <div className="bg-accent px-2">
              <p key={label} className="font-mono text-ui font-semibold tracking-[0.25em] text-foreground uppercase animate-fade-in">
                {label}
              </p>
            </div>
            <div className="flex">
              {personas.map((p) => (
                <button
                  key={p.id}
                  onClick={() => selectPersona(p.id)}
                  className="flex-1 px-4 py-3 font-mono text-detail text-muted-foreground uppercase tracking-widest hover:bg-accent/20 hover:text-accent-foreground transition-colors border-r border-accent/20 last:border-r-0 text-center cursor-pointer"
                >
                  {p.name}
                </button>
              ))}
            </div>
          </div>
        ) : isConfirm ? (
          <div className={glass}>
            <div className="bg-accent px-2">
              <p key={label} className="font-mono text-ui font-semibold tracking-[0.25em] text-foreground uppercase animate-fade-in">
                {label}
              </p>
            </div>
            <Button size="xs" variant="main" onClick={create} loading={loading} className="w-full">
              create companion →
            </Button>
          </div>
        ) : (
          <StandardInput
            ref={inputRef}
            label={label}
            value={input}
            onChange={(v) => { setInput(v); if (error) setError(""); }}
            onSubmit={submit}
            onBack={goBack}
            password={isPassword}
            disabled={loading}
            loading={loading}
            error={error}
          />
        )}

        <div className="w-[80px]" />
      </div>
    </div>
  );
}
