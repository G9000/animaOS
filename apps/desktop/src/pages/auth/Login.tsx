import { useState, useEffect, useRef } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { Button, StandardInput } from "@anima/standard-templates";
import { api, setUnlockToken } from "../../lib/api";
import { useAuth } from "../../context/AuthContext";

type LoginStep = "username" | "password";
type RecoveryStep = "phrase" | "newPwd" | "confirm";

const CACHED_USER_KEY = "anima_last_user";

function readCachedUser(): string {
  try {
    return localStorage.getItem(CACHED_USER_KEY) ?? "";
  } catch {
    return "";
  }
}

export default function Login() {
  const [step, setStep] = useState<LoginStep>(() =>
    readCachedUser() ? "password" : "username",
  );
  const [username, setUsername] = useState<string>(() => readCachedUser());
  const [input, setInput] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [phrase, setPhrase] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const [recoveryStep, setRecoveryStep] = useState<RecoveryStep>("phrase");

  const inputRef = useRef<HTMLInputElement>(null);
  const { setUser, isAuthenticated, isLoading } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    inputRef.current?.focus();
  }, [step, recoveryStep, recovering]);

  if (isLoading) return null;
  if (isAuthenticated) return <Navigate to="/" replace />;

  const isPassword =
    (!recovering && step === "password") ||
    (recovering && (recoveryStep === "newPwd" || recoveryStep === "confirm"));

  const label = recovering
    ? {
        phrase: "12-word recovery phrase",
        newPwd: "new password",
        confirm: "confirm password",
      }[recoveryStep]
    : step === "username"
      ? "who are you?"
      : username
        ? `welcome, ${username}`
        : "password";

  function goBack() {
    setError("");
    if (!recovering) {
      if (step === "password" && !readCachedUser()) {
        setStep("username");
        setInput(username);
      } else navigate("/init", { replace: true });
    } else {
      if (recoveryStep === "phrase") {
        setRecovering(false);
        setInput("");
      } else if (recoveryStep === "newPwd") {
        setRecoveryStep("phrase");
        setInput(phrase);
      } else {
        setRecoveryStep("newPwd");
        setInput("");
      }
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
        try {
          localStorage.setItem(CACHED_USER_KEY, username);
        } catch {
          /* ignore */
        }
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

    if (recoveryStep === "phrase") {
      setPhrase(input.trim().toLowerCase());
      setRecoveryStep("newPwd");
      setInput("");
    } else if (recoveryStep === "newPwd") {
      if (input.length < 8) {
        setError("min 8 characters");
        return;
      }
      setNewPassword(input);
      setRecoveryStep("confirm");
      setInput("");
    } else {
      if (input !== newPassword) {
        setError("doesn't match");
        setInput("");
        return;
      }
      setLoading(true);
      try {
        const res = await api.auth.recover(phrase, newPassword);
        setUnlockToken(res.unlockToken);
        setUser({ id: res.id, username: res.username, name: res.name });
        navigate("/");
      } catch (err) {
        setError(err instanceof Error ? err.message : "recovery failed");
        setRecoveryStep("phrase");
        setPhrase("");
        setNewPassword("");
        setInput("");
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <div className="h-screen w-screen text-foreground overflow-hidden relative">
      <Link
        className="absolute right-6 top-6 z-20 font-mono text-[10px] uppercase tracking-[0.18em] text-muted-foreground hover:text-foreground"
        to="/daemon"
      >
        daemon recovery
      </Link>

      {/* Bottom gradient */}
      <div
        className="absolute inset-0 pointer-events-none z-[1]"
        style={{
          background:
            "radial-gradient(ellipse 80% 50% at 50% 100%, rgba(0,0,0,0.88) 0%, transparent 100%)",
        }}
      />

      {/* Bottom bar */}
      <div className="absolute bottom-0 left-0 right-0 z-10 px-8 pb-10 flex items-end justify-between">
        <div className="bg-background/20 backdrop-blur-[44px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.24)]">
          <Button size="xs" variant="main" onClick={goBack}>← back</Button>
        </div>

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


        {!recovering ? (
          <div className="bg-background/20 backdrop-blur-[44px] border border-foreground/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.24)]">
            <Button size="xs" variant="main" onClick={() => { setRecovering(true); setError(""); setInput(""); }}>
              forgot password?
            </Button>
          </div>
        ) : (
          <div className="w-[112px]" />
        )}
      </div>
    </div>
  );
}
