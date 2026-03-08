import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, setUnlockToken } from "../lib/api";
import { useAuth } from "../context/AuthContext";

export default function Register() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { setUser } = useAuth();
  const navigate = useNavigate();

  async function registerAccount() {
    if (!name || !username || !password) {
      setError("Please fill in all fields");
      return;
    }

    setError("");
    setLoading(true);

    try {
      const user = await api.auth.register(username, password, name);
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
    await registerAccount();
  }

  return (
    <div className="flex items-center justify-center min-h-screen p-8">
      <div className="w-full max-w-[380px]">
        <div className="text-center mb-12">
          <div className="text-4xl text-(--color-text-muted)/10 mb-4 select-none">◈</div>
          <h1 className="font-mono text-lg font-medium tracking-[0.3em] uppercase text-(--color-text)">
            ANIMA
          </h1>
          <p className="text-xs text-(--color-text-muted) mt-1.5 tracking-wide">
            Personal companion
          </p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="bg-(--color-bg-card) border border-(--color-border) rounded-lg p-7 space-y-5"
        >
          {error && (
            <div className="bg-(--color-danger)/8 border border-(--color-danger)/20 text-(--color-danger) px-3.5 py-2.5 rounded-md text-xs">
              {error}
            </div>
          )}

          <div>
            <label
              htmlFor="name"
              className="block text-[11px] font-medium text-(--color-text-muted) mb-1.5 tracking-wide"
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
              className="w-full px-3.5 py-2.5 bg-(--color-bg-input) border border-(--color-border) rounded-md text-sm text-(--color-text) outline-none transition-colors focus:border-(--color-text-muted)/40 placeholder:text-(--color-text-muted)/30"
            />
          </div>

          <div>
            <label
              htmlFor="username"
              className="block text-[11px] font-medium text-(--color-text-muted) mb-1.5 tracking-wide"
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
              className="w-full px-3.5 py-2.5 bg-(--color-bg-input) border border-(--color-border) rounded-md text-sm text-(--color-text) outline-none transition-colors focus:border-(--color-text-muted)/40 placeholder:text-(--color-text-muted)/30"
            />
          </div>

          <div>
            <label
              htmlFor="password"
              className="block text-[11px] font-medium text-(--color-text-muted) mb-1.5 tracking-wide"
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
              className="w-full px-3.5 py-2.5 bg-(--color-bg-input) border border-(--color-border) rounded-md text-sm text-(--color-text) outline-none transition-colors focus:border-(--color-text-muted)/40 placeholder:text-(--color-text-muted)/30"
            />
          </div>

          <button
            type="submit"
            className="w-full py-2.5 bg-(--color-text) text-(--color-bg) rounded-md text-sm font-medium tracking-wide cursor-pointer transition-colors hover:bg-(--color-primary-hover) disabled:opacity-40 disabled:cursor-not-allowed"
            disabled={loading}
          >
            {loading ? "Creating..." : "Create account"}
          </button>
        </form>

        <p className="text-center mt-6 text-xs text-(--color-text-muted)">
          Already have an account?{" "}
          <Link
            to="/login"
            className="text-(--color-text) hover:opacity-70 underline underline-offset-4"
          >
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}
