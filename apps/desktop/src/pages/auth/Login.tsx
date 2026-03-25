import { useState, useEffect, useRef } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { useAnimaSymbol, useGlowLine, Button } from "@anima/standard-templates";
import { api, setUnlockToken } from "../../lib/api";
import { useAuth } from "../../context/AuthContext";
import { TerminalInput } from "../init/TerminalInput";

type LoginStep = "username" | "password";
type RecoveryStep = "phrase" | "newPwd" | "confirm";

export default function Login() {
  const [step, setStep] = useState<LoginStep>("username");
  const [username, setUsername] = useState("");
  const [input, setInput] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [phrase, setPhrase] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [isFocused, setIsFocused] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [recoveryStep, setRecoveryStep] = useState<RecoveryStep>("phrase");

  const inputRef = useRef<HTMLInputElement>(null);
  const { setUser, isAuthenticated, isLoading } = useAuth();
  const navigate = useNavigate();

  const symbolSpeed = isFocused ? 1.5 : 0.7;
  const animaSymbol = useAnimaSymbol(symbolSpeed);
  const glowLine = useGlowLine(isFocused, 28);

  useEffect(() => {
    inputRef.current?.focus();
  }, [step, recoveryStep, recovering]);

  if (isLoading) return null;
  if (isAuthenticated) return <Navigate to="/" replace />;

  const isPassword =
    (!recovering && step === "password") ||
    (recovering && (recoveryStep === "newPwd" || recoveryStep === "confirm"));

  const prompt = recovering
    ? { phrase: "12-word recovery phrase", newPwd: "new password", confirm: "confirm password" }[recoveryStep]
    : { username: "who are you?", password: "password" }[step];

  function goBack() {
    setError("");
    if (!recovering) {
      if (step === "password") { setStep("username"); setInput(username); }
    } else {
      if (recoveryStep === "phrase") { setRecovering(false); setInput(""); }
      else if (recoveryStep === "newPwd") { setRecoveryStep("phrase"); setInput(phrase); }
      else { setRecoveryStep("newPwd"); setInput(""); }
    }
  }

  async function submit() {
    if (!input.trim() || loading) return;
    setError("");

    if (!recovering) {
      if (step === "username") {
        setUsername(input.trim());
        setStep("password");
        setInput("");
        return;
      }
      setLoading(true);
      try {
        const res = await api.auth.login(username, input);
        setUnlockToken(res.unlockToken);
        setUser({ id: res.id, username: res.username, name: res.name });
        navigate("/");
      } catch (err) {
        setError(err instanceof Error ? err.message : "access denied");
        setInput("");
      } finally {
        setLoading(false);
      }
      return;
    }

    // Recovery flow
    if (recoveryStep === "phrase") {
      setPhrase(input.trim().toLowerCase());
      setRecoveryStep("newPwd");
      setInput("");
    } else if (recoveryStep === "newPwd") {
      if (input.length < 8) { setError("min 8 characters"); return; }
      setNewPassword(input);
      setRecoveryStep("confirm");
      setInput("");
    } else {
      if (input !== newPassword) { setError("doesn't match"); setInput(""); return; }
      setLoading(true);
      try {
        const res = await api.auth.recover(phrase, newPassword);
        setUnlockToken(res.unlockToken);
        setUser({ id: res.id, username: res.username, name: res.name });
        navigate("/");
      } catch (err) {
        setError(err instanceof Error ? err.message : "recovery failed");
        setRecoveryStep("phrase");
        setPhrase(""); setNewPassword(""); setInput("");
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <div className="h-screen w-screen bg-background text-foreground flex flex-col overflow-hidden">
      {/* Anima symbol — centered */}
      <div className="flex-1 flex items-center justify-center pointer-events-none min-h-0">
        <div className="scale-[0.5] sm:scale-[0.7] origin-center">
          <pre className="text-body whitespace-pre leading-none text-foreground/40 bg-transparent">
            {animaSymbol.base}
          </pre>
        </div>
      </div>

      {/* Input area */}
      <div className="shrink-0 px-8 pb-12">
        <div className="w-full max-w-sm mx-auto flex flex-col items-center gap-3">

          {/* Confirmed username breadcrumb */}
          {!recovering && step === "password" && (
            <span className="font-mono text-label text-subtle-foreground tracking-widest uppercase animate-fade-in">
              {username}
            </span>
          )}

          {/* Current prompt */}
          <p
            key={prompt}
            className="font-mono text-ui text-muted-foreground tracking-widest uppercase text-center animate-fade-in"
          >
            {prompt}
          </p>

          {/* Error */}
          {error && (
            <p className="font-mono text-detail text-destructive animate-fade-in">
              [err] {error}
            </p>
          )}

          {/* Terminal input */}
          <TerminalInput
            inputRef={inputRef}
            value={input}
            onChange={setInput}
            onSubmit={submit}
            onBack={goBack}
            onFocus={() => setIsFocused(true)}
            onBlur={() => setIsFocused(false)}
            placeholder=""
            password={isPassword}
            disabled={loading}
            isFocused={isFocused}
            glowLine={glowLine}
          />

          {/* Forgot password */}
          {!recovering ? (
            <Button
              size="xs"
              variant="ghost"
              onClick={() => { setRecovering(true); setError(""); setInput(""); }}
            >
              forgot password?
            </Button>
          ) : (
            <Button
              size="xs"
              variant="ghost"
              onClick={goBack}
            >
              ← back
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
