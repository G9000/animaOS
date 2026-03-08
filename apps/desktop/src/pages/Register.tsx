import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, setUnlockToken, type PersonaTemplate } from "../lib/api";
import { useAuth } from "../context/AuthContext";
import defaultAvatar from "../assets/persona-default.svg";
import aliceAvatar from "../assets/persona-alice.svg";

type RegisterStep = "account" | "persona";

type PersonaOption = {
  id: PersonaTemplate;
  name: string;
  brief: string;
  description: string;
  image: string;
  gradient: string;
};

const PERSONA_OPTIONS: PersonaOption[] = [
  {
    id: "default",
    name: "Brief Default",
    brief: "Minimal and direct",
    description: "Short, practical responses focused on immediate next steps.",
    image: defaultAvatar,
    gradient: "linear-gradient(135deg, #8A93A8 0%, #4F5767 100%)",
  },
  {
    id: "alice",
    name: "Alice",
    brief: "Warm and attentive",
    description: "Gentle emotional support with grounded clarity and realistic action.",
    image: aliceAvatar,
    gradient: "linear-gradient(135deg, #F7CDAE 0%, #D38F7A 100%)",
  },
];

export default function Register() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [step, setStep] = useState<RegisterStep>("account");
  const [personaTemplate, setPersonaTemplate] =
    useState<PersonaTemplate>("default");
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

  function continueToPersonaStep() {
    if (!validateAccountFields()) return;
    setError("");
    setStep("persona");
  }

  async function registerAccount() {
    if (!validateAccountFields()) return;

    setError("");
    setLoading(true);

    try {
      const user = await api.auth.register(
        username,
        password,
        name,
        personaTemplate,
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
      continueToPersonaStep();
      return;
    }
    await registerAccount();
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-(--color-bg)">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute -top-24 left-1/2 h-56 w-56 -translate-x-1/2 rounded-full bg-(--color-primary)/10 blur-3xl" />
        <div className="absolute bottom-0 left-0 h-64 w-64 rounded-full bg-(--color-text-muted)/10 blur-3xl" />
        <div className="absolute right-0 top-1/3 h-52 w-52 rounded-full bg-(--color-primary-hover)/10 blur-3xl" />
      </div>

      <div className="relative mx-auto flex min-h-screen w-full max-w-[980px] items-center px-6 py-10">
        <div className="mx-auto w-full max-w-[760px]">
          <form
            onSubmit={handleSubmit}
            className="rounded-2xl border border-(--color-border) bg-(--color-bg-card) p-7 shadow-[0_24px_70px_rgba(0,0,0,0.35)]"
          >
            <div className="space-y-2">
              <div className="flex items-center justify-between text-[11px] tracking-wide text-(--color-text-muted)">
                <span>Step {isAccountStep ? "1" : "2"} of 2</span>
                <span>{isAccountStep ? "Account details" : "Persona selection"}</span>
              </div>
              <div className="h-1.5 rounded-full bg-(--color-bg-input)">
                <div
                  className={`h-full rounded-full bg-(--color-text) transition-all duration-300 ${
                    isAccountStep ? "w-1/2" : "w-full"
                  }`}
                />
              </div>
            </div>

            <div className="mt-6 mb-5">
              <h2 className="font-mono text-sm tracking-[0.14em] uppercase text-(--color-text)">
                {isAccountStep ? "Create Your Local Vault" : "Choose Your AI Companion"}
              </h2>
              <p className="mt-1 text-xs text-(--color-text-muted)">
                {isAccountStep
                  ? "These credentials unlock your encrypted local data."
                  : "You can change persona later in settings, but this sets your initial voice."}
              </p>
            </div>

            {error && (
              <div className="mb-5 rounded-md border border-(--color-danger)/25 bg-(--color-danger)/8 px-3.5 py-2.5 text-xs text-(--color-danger)">
                {error}
              </div>
            )}

            {isAccountStep ? (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="name"
                    className="mb-1.5 block text-[11px] font-medium tracking-wide text-(--color-text-muted)"
                  >
                    Name
                  </label>
                  <input
                    id="name"
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Your name"
                    required
                    autoFocus
                    className="w-full rounded-md border border-(--color-border) bg-(--color-bg-input) px-3.5 py-2.5 text-sm text-(--color-text) outline-none transition-colors focus:border-(--color-text-muted)/40 placeholder:text-(--color-text-muted)/30"
                  />
                </div>

                <div>
                  <label
                    htmlFor="username"
                    className="mb-1.5 block text-[11px] font-medium tracking-wide text-(--color-text-muted)"
                  >
                    Username
                  </label>
                  <input
                    id="username"
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="Choose a username"
                    required
                    className="w-full rounded-md border border-(--color-border) bg-(--color-bg-input) px-3.5 py-2.5 text-sm text-(--color-text) outline-none transition-colors focus:border-(--color-text-muted)/40 placeholder:text-(--color-text-muted)/30"
                  />
                </div>

                <div className="sm:col-span-2">
                  <label
                    htmlFor="password"
                    className="mb-1.5 block text-[11px] font-medium tracking-wide text-(--color-text-muted)"
                  >
                    Password
                  </label>
                  <input
                    id="password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Min 6 characters"
                    required
                    minLength={6}
                    className="w-full rounded-md border border-(--color-border) bg-(--color-bg-input) px-3.5 py-2.5 text-sm text-(--color-text) outline-none transition-colors focus:border-(--color-text-muted)/40 placeholder:text-(--color-text-muted)/30"
                  />
                </div>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  {PERSONA_OPTIONS.map((option) => {
                    const selected = option.id === personaTemplate;
                    return (
                      <button
                        key={option.id}
                        type="button"
                        onClick={() => setPersonaTemplate(option.id)}
                        className={`group relative overflow-hidden rounded-xl border p-4 text-left transition-all duration-200 cursor-pointer ${
                          selected
                            ? "border-(--color-text)/45 bg-(--color-bg-input) shadow-[0_10px_32px_rgba(0,0,0,0.35)]"
                            : "border-(--color-border) hover:border-(--color-text-muted)/45 hover:-translate-y-0.5"
                        }`}
                      >
                        <div
                          className="absolute inset-0 opacity-20"
                          style={{ background: option.gradient }}
                        />
                        <div className="relative">
                          <div className="flex items-start justify-between gap-3">
                            <div className="flex items-center gap-3 min-w-0">
                              <img
                                src={option.image}
                                alt={`${option.name} profile`}
                                className="h-12 w-12 rounded-full border border-(--color-border)"
                              />
                              <div className="min-w-0">
                                <p className="truncate text-sm font-medium text-(--color-text)">
                                  {option.name}
                                </p>
                                <p className="truncate text-[11px] text-(--color-text-muted)">
                                  {option.brief}
                                </p>
                              </div>
                            </div>
                            <span
                              className={`rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wide ${
                                selected
                                  ? "bg-(--color-text) text-(--color-bg)"
                                  : "border border-(--color-border) text-(--color-text-muted)"
                              }`}
                            >
                              {selected ? "Selected" : "Choose"}
                            </span>
                          </div>
                          <p className="mt-3 text-xs leading-relaxed text-(--color-text-muted)">
                            {option.description}
                          </p>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="mt-6 flex items-center gap-3">
              {!isAccountStep && (
                <button
                  type="button"
                  onClick={() => {
                    setError("");
                    setStep("account");
                  }}
                  className="rounded-md border border-(--color-border) px-4 py-2.5 text-sm text-(--color-text) transition-colors cursor-pointer hover:border-(--color-text-muted)/45"
                >
                  Back
                </button>
              )}
              <button
                type="submit"
                className="flex-1 rounded-md bg-(--color-text) py-2.5 text-sm font-medium tracking-wide text-(--color-bg) transition-colors cursor-pointer hover:bg-(--color-primary-hover) disabled:cursor-not-allowed disabled:opacity-40"
                disabled={loading}
              >
                {isAccountStep
                  ? "Continue"
                  : loading
                    ? "Creating..."
                    : "Create account"}
              </button>
            </div>

            <p className="mt-6 text-center text-xs text-(--color-text-muted)">
              Already have an account?{" "}
              <Link
                to="/login"
                className="text-(--color-text) underline underline-offset-4 transition-opacity hover:opacity-70"
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
