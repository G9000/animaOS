import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import { useAuth } from "../context/AuthContext";

export default function Profile() {
  const { user, setUser, logout } = useAuth();
  const navigate = useNavigate();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(user?.name || "");
  const [gender, setGender] = useState(user?.gender || "");
  const [age, setAge] = useState(user?.age?.toString() || "");
  const [birthday, setBirthday] = useState(user?.birthday || "");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  if (!user) return null;

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);

    try {
      const updated = await api.users.update(user!.id, {
        name,
        gender: gender || null,
        age: age ? parseInt(age, 10) : null,
        birthday: birthday || null,
      });
      setUser(updated);
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete() {
    if (!confirm("Are you sure? This will permanently delete your account."))
      return;

    try {
      await api.users.delete(user!.id);
      await logout();
      navigate("/login");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-[520px] mx-auto px-8 py-8">
        {/* Header */}
        <div className="flex items-center gap-4 mb-8">
          <div className="w-12 h-12 rounded-sm bg-foreground text-background flex items-center justify-center text-lg font-bold shrink-0">
            {user.name.charAt(0).toUpperCase()}
          </div>
          <div>
            <h1 className="text-base font-semibold">{user.name}</h1>
            <p className="text-xs text-muted-foreground">@{user.username}</p>
          </div>
        </div>

        {error && (
          <div className="bg-destructive/10 border border-destructive/30 text-destructive px-3 py-2 rounded-sm text-xs mb-4">
            {error}
          </div>
        )}

        {editing ? (
          <form
            onSubmit={handleSave}
            className="bg-card border border-border rounded-sm p-5 space-y-4"
          >
            <Field label="Name">
              <input
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
                className="w-full px-3 py-2 bg-input border border-border rounded-sm text-sm text-foreground outline-none focus:border-text-muted transition-colors"
              />
            </Field>
            <Field label="Gender">
              <select
                value={gender}
                onChange={(e) => setGender(e.target.value)}
                className="w-full px-3 py-2 bg-input border border-border rounded-sm text-sm text-foreground outline-none focus:border-text-muted transition-colors"
              >
                <option value="">—</option>
                <option value="male">Male</option>
                <option value="female">Female</option>
                <option value="other">Other</option>
              </select>
            </Field>
            <Field label="Age">
              <input
                type="number"
                value={age}
                onChange={(e) => setAge(e.target.value)}
                min="1"
                max="150"
                className="w-full px-3 py-2 bg-input border border-border rounded-sm text-sm text-foreground outline-none focus:border-text-muted transition-colors"
              />
            </Field>
            <Field label="Birthday">
              <input
                type="date"
                value={birthday}
                onChange={(e) => setBirthday(e.target.value)}
                className="w-full px-3 py-2 bg-input border border-border rounded-sm text-sm text-foreground outline-none focus:border-text-muted transition-colors"
              />
            </Field>
            <div className="flex gap-3 pt-2">
              <button
                type="submit"
                disabled={saving}
                className="px-4 py-2 bg-foreground text-background rounded-sm text-xs uppercase tracking-wider font-semibold hover:bg-primary/80 disabled:opacity-40 transition-colors"
              >
                {saving ? "Saving..." : "Save"}
              </button>
              <button
                type="button"
                onClick={() => setEditing(false)}
                className="px-4 py-2 border border-border text-muted-foreground rounded-sm text-xs uppercase tracking-wider hover:text-foreground hover:border-text-muted transition-colors"
              >
                Cancel
              </button>
            </div>
          </form>
        ) : (
          <div className="bg-card border border-border rounded-sm p-5">
            <div className="space-y-0">
              <InfoRow label="Username" value={user.username} />
              <InfoRow label="Name" value={user.name} />
              <InfoRow label="Gender" value={user.gender || "—"} />
              <InfoRow label="Age" value={user.age ? String(user.age) : "—"} />
              <InfoRow label="Birthday" value={user.birthday || "—"} />
              <InfoRow
                label="Joined"
                value={
                  user.createdAt
                    ? new Date(user.createdAt).toLocaleDateString()
                    : "—"
                }
                last
              />
            </div>
            <div className="flex gap-3 mt-5">
              <button
                onClick={() => setEditing(true)}
                className="px-4 py-2 bg-foreground text-background rounded-sm text-xs uppercase tracking-wider font-semibold hover:bg-primary/80 transition-colors"
              >
                Edit
              </button>
              <button
                onClick={handleDelete}
                className="px-4 py-2 border border-border text-muted-foreground rounded-sm text-xs uppercase tracking-wider hover:text-destructive hover:border-destructive/30 transition-colors"
              >
                Delete Account
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-[11px] text-muted-foreground mb-1.5 uppercase tracking-wider">
        {label}
      </label>
      {children}
    </div>
  );
}

function InfoRow({
  label,
  value,
  last,
}: {
  label: string;
  value: string;
  last?: boolean;
}) {
  return (
    <div
      className={`flex justify-between py-2.5 text-xs ${last ? "" : "border-b border-border"}`}
    >
      <span className="text-muted-foreground uppercase tracking-wider">
        {label}
      </span>
      <span>{value}</span>
    </div>
  );
}
