import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Field } from "@anima/ui";
import { api, setUnlockToken } from "../lib/api";
import { useAuth } from "../context/AuthContext";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { setUser } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const res = await api.auth.login(username, password);
      setUnlockToken(res.unlockToken);
      setUser({ id: res.id, username: res.username, name: res.name });
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Access denied");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen p-8">
      <div className="w-full max-w-[380px]">
        <div className="text-center mb-12">
          <div className="text-4xl text-text-muted/10 mb-4 select-none">
            ◈
          </div>
          <h1 className="font-mono text-lg font-medium tracking-[0.3em] uppercase text-text">
            ANIMA
          </h1>
        </div>

        <form onSubmit={handleSubmit} className="space-y-3">
          {error && (
            <div className="bg-danger/8 border border-danger/20 text-danger px-3.5 py-2.5 rounded-md text-xs">
              {error}
            </div>
          )}

          <Field
            label="Username"
            id="username"
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="Enter username"
            required
            autoFocus
          />

          <Field
            label="Password"
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Enter password"
            required
          />

          <button
            type="submit"
            className="w-full py-2.5 bg-text text-bg rounded-md text-sm font-medium tracking-wide cursor-pointer transition-colors hover:bg-primary-hover disabled:opacity-40 disabled:cursor-not-allowed"
            disabled={loading}
          >
            {loading ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <p className="text-center mt-6 text-xs text-text-muted">
          Don't have an account?{" "}
          <Link
            to="/register"
            className="text-text hover:opacity-70 underline underline-offset-4"
          >
            Create one
          </Link>
        </p>
      </div>
    </div>
  );
}
