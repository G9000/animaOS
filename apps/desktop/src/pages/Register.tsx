import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Field } from "@anima/ui";
import { api, setUnlockToken } from "../lib/api";
import { useAuth } from "../context/AuthContext";

type RegisterStep = "account" | "create-ai";

export default function Register() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [agentName, setAgentName] = useState("Anima");
  const [step, setStep] = useState<RegisterStep>("account");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { setUser } = useAuth();
  const navigate = useNavigate();

  const isAccountStep = step === "account";

  function validateAccountFields(): boolean {
    if (!name || !username || !password) {
      setError("Please fill in all fields");
      return false;
    }
    if (password.length < 6) {
      setError("Password must be at least 6 characters");
      return false;
    }
    return true;
  }

  function continueToCreateAI() {
    if (!validateAccountFields()) return;
    setError("");
    setStep("create-ai");
  }

  async function registerAccount() {
    if (!agentName.trim()) {
      setError("Give your AI a name");
      return;
    }
    setError("");
    setLoading(true);

    try {
      const user = await api.auth.register(
        username,
        password,
        name,
        "default",
        agentName.trim(),
      );
      setUnlockToken(user.unlockToken);
      setUser({ id: user.id, username: user.username, name: user.name });
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (isAccountStep) {
      continueToCreateAI();
      return;
    }
    await registerAccount();
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-bg">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -top-24 left-1/2 h-56 w-56 -translate-x-1/2 rounded-full bg-primary/10 blur-3xl" />
        <div className="absolute bottom-0 left-0 h-64 w-64 rounded-full bg-text-muted/10 blur-3xl" />
        <div className="absolute right-0 top-1/3 h-52 w-52 rounded-full bg-primary-hover/10 blur-3xl" />
      </div>

      <div className="relative mx-auto flex min-h-screen w-full max-w-[980px] items-center px-6 py-10">
        <div className="mx-auto w-full max-w-[460px]">
          <form
            onSubmit={handleSubmit}
            className="rounded-2xl border border-border bg-bg-card p-7 shadow-[0_24px_70px_rgba(0,0,0,0.35)]"
          >
            {/* Progress bar */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-[11px] tracking-wide text-text-muted">
                <span>Step {isAccountStep ? "1" : "2"} of 2</span>
                <span>
                  {isAccountStep ? "Account details" : "Name your AI"}
                </span>
              </div>
              <div className="h-1.5 rounded-full bg-bg-input">
                <div
                  className={`h-full rounded-full bg-text transition-all duration-300 ${
                    isAccountStep ? "w-1/2" : "w-full"
                  }`}
                />
              </div>
            </div>

            {/* Header */}
            <div className="mt-6 mb-5">
              <h2 className="font-mono text-sm tracking-[0.14em] uppercase text-text">
                {isAccountStep ? "Create Your Local Vault" : "Name Your AI"}
              </h2>
              <p className="mt-1 text-xs text-text-muted">
                {isAccountStep
                  ? "These credentials unlock your encrypted local data."
                  : "This becomes its permanent identity — choose wisely."}
              </p>
            </div>

            {error && (
              <div className="mb-5 rounded-md border border-danger/25 bg-danger/8 px-3.5 py-2.5 text-xs text-danger">
                {error}
              </div>
            )}

            {/* Step content */}
            {isAccountStep ? (
              <div className="space-y-4">
                <Field
                  label="Your Name"
                  id="name"
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Your name"
                  required
                  autoFocus
                />

                <Field
                  label="Username"
                  id="username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="Choose a username"
                  required
                />

                <Field
                  label="Password"
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Min 6 characters"
                  required
                  minLength={6}
                />
              </div>
            ) : (
              <Field
                label="AI Name"
                id="agentName"
                type="text"
                value={agentName}
                onChange={(e) => setAgentName(e.target.value)}
                placeholder="Anima"
                required
                autoFocus
                maxLength={50}
                hint="You can always change how it behaves later in settings."
              />
            )}

            {/* Actions */}
            <div className="mt-6 flex items-center gap-3">
              {!isAccountStep && (
                <button
                  type="button"
                  onClick={() => {
                    setError("");
                    setStep("account");
                  }}
                  className="rounded-md border border-border px-4 py-2.5 text-sm text-text transition-colors cursor-pointer hover:border-text-muted/45"
                >
                  Back
                </button>
              )}
              <button
                type="submit"
                className="flex-1 rounded-md bg-text py-2.5 text-sm font-medium tracking-wide text-bg transition-colors cursor-pointer hover:bg-primary-hover disabled:cursor-not-allowed disabled:opacity-40"
                disabled={loading}
              >
                {isAccountStep ? "Continue" : loading ? "Creating…" : "Create"}
              </button>
            </div>

            <p className="mt-6 text-center text-xs text-text-muted">
              Already have an account?{" "}
              <Link
                to="/login"
                className="text-text underline underline-offset-4 transition-opacity hover:opacity-70"
              >
                Sign in
              </Link>
            </p>
          </form>
        </div>
      </div>
    </div>
  );
}
