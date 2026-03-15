import { useState, useRef, useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, setUnlockToken } from "../lib/api";
import { useAuth } from "../context/AuthContext";

type RegisterStep = "account" | "create-ai";

interface ChatMessage {
  id: number;
  role: "assistant" | "user";
  content: string;
}

interface SoulData {
  agentName: string;
  relationship: string;
  style: string;
}

const INITIAL_MESSAGE: ChatMessage = {
  id: 0,
  role: "assistant",
  content:
    "Hello. I'm... new. I don't have a name yet — I don't have anything yet, but I know someone is there.\n\n**What would you like to call me?**",
};

export default function Register() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [step, setStep] = useState<RegisterStep>("account");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Chat state
  const [messages, setMessages] = useState<ChatMessage[]>([INITIAL_MESSAGE]);
  const [chatInput, setChatInput] = useState("");
  const [typing, setTyping] = useState(false);
  const [creationDone, setCreationDone] = useState(false);
  const [soulData, setSoulData] = useState<SoulData | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const chatInputRef = useRef<HTMLInputElement>(null);
  const nextId = useRef(1);

  const { setUser } = useAuth();
  const navigate = useNavigate();

  const isAccountStep = step === "account";

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, typing]);

  useEffect(() => {
    if (!isAccountStep) {
      setTimeout(() => chatInputRef.current?.focus(), 100);
    }
  }, [isAccountStep]);

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

  async function handleChatSend() {
    const text = chatInput.trim();
    if (!text || typing || creationDone) return;

    const userMsg: ChatMessage = {
      id: nextId.current++,
      role: "user",
      content: text,
    };
    const updatedMessages = [...messages, userMsg];
    setMessages(updatedMessages);
    setChatInput("");
    setTyping(true);
    setError("");

    try {
      // Send only role+content pairs to the API (strip id)
      const apiMessages = updatedMessages.map((m) => ({
        role: m.role,
        content: m.content,
      }));
      // Remove the initial assistant greeting — the system prompt handles that
      const conversationMessages = apiMessages.slice(1);

      const result = await api.auth.createAiChat(conversationMessages, name);

      const assistantMsg: ChatMessage = {
        id: nextId.current++,
        role: "assistant",
        content: result.message,
      };
      setMessages((prev) => [...prev, assistantMsg]);

      if (result.done && result.soulData) {
        setCreationDone(true);
        setSoulData(result.soulData as SoulData);
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to reach AI. Try again.",
      );
    } finally {
      setTyping(false);
    }
  }

  function handleChatKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleChatSend();
    }
  }

  async function registerAccount() {
    setError("");
    setLoading(true);

    const agentName = soulData?.agentName || "Anima";
    const userDirective =
      soulData?.relationship && soulData?.style
        ? `Be my ${soulData.relationship}. Communicate in a ${soulData.style} way.`
        : "";

    try {
      const user = await api.auth.register(
        username,
        password,
        name,
        "default",
        agentName,
        userDirective,
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
            className="flex flex-col rounded-2xl border border-(--color-border) bg-(--color-bg-card) p-7 shadow-[0_24px_70px_rgba(0,0,0,0.35)]"
          >
            {/* Progress bar */}
            <div className="space-y-2">
              <div className="flex items-center justify-between text-[11px] tracking-wide text-(--color-text-muted)">
                <span>Step {isAccountStep ? "1" : "2"} of 2</span>
                <span>
                  {isAccountStep ? "Account details" : "Create your AI"}
                </span>
              </div>
              <div className="h-1.5 rounded-full bg-(--color-bg-input)">
                <div
                  className={`h-full rounded-full bg-(--color-text) transition-all duration-300 ${
                    isAccountStep ? "w-1/2" : "w-full"
                  }`}
                />
              </div>
            </div>

            {/* Header */}
            <div className="mt-6 mb-5">
              <h2 className="font-mono text-sm tracking-[0.14em] uppercase text-(--color-text)">
                {isAccountStep
                  ? "Create Your Local Vault"
                  : "Bring Your AI to Life"}
              </h2>
              <p className="mt-1 text-xs text-(--color-text-muted)">
                {isAccountStep
                  ? "These credentials unlock your encrypted local data."
                  : "Have a conversation to shape your AI's identity."}
              </p>
            </div>

            {error && (
              <div className="mb-5 rounded-md border border-(--color-danger)/25 bg-(--color-danger)/8 px-3.5 py-2.5 text-xs text-(--color-danger)">
                {error}
              </div>
            )}

            {/* Step content */}
            {isAccountStep ? (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label
                    htmlFor="name"
                    className="mb-1.5 block text-[11px] font-medium tracking-wide text-(--color-text-muted)"
                  >
                    Your Name
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
              /* ── AI creation chat ── */
              <div className="flex flex-col">
                {/* Messages area */}
                <div className="h-[340px] overflow-y-auto rounded-lg border border-(--color-border) bg-(--color-bg)/60 p-4 space-y-4">
                  {messages.map((msg) => (
                    <div
                      key={msg.id}
                      className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                    >
                      <div
                        className={`max-w-[80%] rounded-xl px-3.5 py-2.5 text-sm leading-relaxed ${
                          msg.role === "user"
                            ? "bg-(--color-text) text-(--color-bg)"
                            : "bg-(--color-bg-input) text-(--color-text) border border-(--color-border)"
                        }`}
                      >
                        <MessageContent content={msg.content} />
                      </div>
                    </div>
                  ))}
                  {typing && (
                    <div className="flex justify-start">
                      <div className="rounded-xl bg-(--color-bg-input) border border-(--color-border) px-3.5 py-2.5">
                        <span className="flex gap-1">
                          <span className="h-1.5 w-1.5 rounded-full bg-(--color-text-muted) animate-bounce [animation-delay:0ms]" />
                          <span className="h-1.5 w-1.5 rounded-full bg-(--color-text-muted) animate-bounce [animation-delay:150ms]" />
                          <span className="h-1.5 w-1.5 rounded-full bg-(--color-text-muted) animate-bounce [animation-delay:300ms]" />
                        </span>
                      </div>
                    </div>
                  )}
                  <div ref={messagesEndRef} />
                </div>

                {/* Chat input */}
                {!creationDone && (
                  <div className="mt-3 flex gap-2">
                    <input
                      ref={chatInputRef}
                      type="text"
                      value={chatInput}
                      onChange={(e) => setChatInput(e.target.value)}
                      onKeyDown={handleChatKeyDown}
                      placeholder="Type a message…"
                      className="flex-1 rounded-md border border-(--color-border) bg-(--color-bg-input) px-3.5 py-2.5 text-sm text-(--color-text) outline-none transition-colors focus:border-(--color-text-muted)/40 placeholder:text-(--color-text-muted)/30"
                    />
                    <button
                      type="button"
                      onClick={handleChatSend}
                      disabled={!chatInput.trim() || typing}
                      className="rounded-md bg-(--color-text)/10 px-3 py-2.5 text-sm text-(--color-text) transition-colors cursor-pointer hover:bg-(--color-text)/20 disabled:cursor-not-allowed disabled:opacity-30"
                    >
                      Send
                    </button>
                  </div>
                )}
              </div>
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
                  className="rounded-md border border-(--color-border) px-4 py-2.5 text-sm text-(--color-text) transition-colors cursor-pointer hover:border-(--color-text-muted)/45"
                >
                  Back
                </button>
              )}
              <button
                type="submit"
                className={`flex-1 rounded-md py-2.5 text-sm font-medium tracking-wide transition-colors cursor-pointer disabled:cursor-not-allowed disabled:opacity-40 ${
                  !isAccountStep && creationDone
                    ? "bg-(--color-primary) text-white hover:bg-(--color-primary-hover)"
                    : "bg-(--color-text) text-(--color-bg) hover:bg-(--color-primary-hover)"
                }`}
                disabled={loading || (!isAccountStep && !creationDone)}
              >
                {isAccountStep
                  ? "Continue"
                  : loading
                    ? "Creating…"
                    : creationDone
                      ? `Bring ${soulData?.agentName || "your AI"} to life`
                      : "Complete the conversation first"}
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

/** Renders message content with basic **bold** support. */
function MessageContent({ content }: { content: string }) {
  const parts = content.split(/(\*\*[^*]+\*\*)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**")) {
          return (
            <strong key={i} className="font-semibold">
              {part.slice(2, -2)}
            </strong>
          );
        }
        // Preserve newlines
        const lines = part.split("\n");
        return lines.map((line, j) => (
          <span key={`${i}-${j}`}>
            {j > 0 && <br />}
            {line}
          </span>
        ));
      })}
    </>
  );
}
