import { useMods, useModEvents, getModClient } from "../lib/mod-client";
import ModCard from "../components/mods/ModCard";
import { useCallback, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

const STATUS_FILTERS = ["all", "running", "stopped", "error"] as const;

export default function Mods() {
  const { mods, loading, error, refresh } = useMods();
  const [showInstall, setShowInstall] = useState(false);
  const [installSource, setInstallSource] = useState("");
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<(typeof STATUS_FILTERS)[number]>("all");
  const [busyMod, setBusyMod] = useState<string | null>(null);
  const [actionErrors, setActionErrors] = useState<Record<string, string>>({});
  const navigate = useNavigate();

  // Real-time status updates
  useModEvents(useCallback(() => {
    void refresh();
  }, [refresh]));

  const handleToggle = async (id: string, enable: boolean) => {
    setBusyMod(id);
    setActionErrors((prev) => ({ ...prev, [id]: "" }));
    try {
      const client = getModClient();
      if (enable) {
        await client.enableMod(id);
      } else {
        await client.disableMod(id);
      }
      await refresh();
    } catch (e) {
      setActionErrors((prev) => ({
        ...prev,
        [id]: e instanceof Error ? e.message : "Action failed",
      }));
    } finally {
      setBusyMod(null);
    }
  };

  const handleInstall = async () => {
    if (!installSource.trim()) return;
    setInstalling(true);
    setInstallError(null);
    try {
      const client = getModClient();
      const data = await client.installMod(installSource.trim());
      setShowInstall(false);
      setInstallSource("");
      await refresh();
      if (data?.id) navigate(`/mods/${data.id}`);
    } catch (e) {
      setInstallError(e instanceof Error ? e.message : "Install failed");
    } finally {
      setInstalling(false);
    }
  };

  const counts = useMemo(() => {
    return mods.reduce(
      (acc, mod) => {
        acc.all += 1;
        if (mod.status === "running") acc.running += 1;
        else if (mod.status === "error") acc.error += 1;
        else acc.stopped += 1;
        return acc;
      },
      { all: 0, running: 0, stopped: 0, error: 0 },
    );
  }, [mods]);

  const filteredMods = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return mods.filter((mod) => {
      const matchesStatus = statusFilter === "all" || mod.status === statusFilter;
      const matchesQuery = !normalizedQuery || mod.id.toLowerCase().includes(normalizedQuery);
      return matchesStatus && matchesQuery;
    });
  }, [mods, query, statusFilter]);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <span className="font-mono text-[10px] text-muted-foreground/40 tracking-widest">
          LOADING MODULES...
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <span className="font-mono text-[10px] text-destructive tracking-wider">
          ANIMA-MOD NOT RUNNING
        </span>
        <span className="font-mono text-[8px] text-muted-foreground/40">
          {error}
        </span>
        <button
          onClick={refresh}
          className="font-mono text-[9px] text-primary border border-primary/30 px-3 py-1 hover:bg-primary/10 transition-colors"
        >
          RETRY
        </button>
      </div>
    );
  }

  return (
    <div className="h-full overflow-auto p-6">
      <div className="max-w-5xl mx-auto">
        <div className="flex flex-col gap-4 mb-6">
          <div className="flex items-center gap-3">
            <h1 className="font-mono text-[11px] tracking-widest text-muted-foreground/60">
              MODULES
            </h1>
            <button
              onClick={() => void refresh()}
              className="font-mono text-[8px] text-muted-foreground/40 border border-border px-2 py-1 hover:text-foreground hover:border-text-muted/30 transition-colors ml-auto"
              title="Refresh modules"
            >
              REFRESH
            </button>
            <button
              onClick={() => setShowInstall(true)}
              className="font-mono text-[8px] text-primary border border-primary/30 px-2 py-1 hover:bg-primary/10 transition-colors"
            >
              ADD
            </button>
          </div>

          <div className="flex flex-col sm:flex-row gap-2 sm:items-center">
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="search modules"
              className="bg-input border border-border px-3 py-2 font-mono text-[10px] text-foreground focus:border-primary/50 outline-none sm:w-64"
            />
            <div className="flex flex-wrap gap-1">
              {STATUS_FILTERS.map((status) => (
                <button
                  key={status}
                  onClick={() => setStatusFilter(status)}
                  className={`font-mono text-[8px] tracking-widest border px-2 py-1 transition-colors ${
                    statusFilter === status
                      ? "border-primary text-primary"
                      : "border-border text-muted-foreground/40 hover:text-foreground"
                  }`}
                >
                  {status.toUpperCase()} {counts[status]}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {filteredMods.map((mod) => (
            <ModCard
              key={mod.id}
              id={mod.id}
              version={mod.version}
              status={mod.status}
              enabled={mod.enabled}
              hasConfigSchema={mod.hasConfigSchema}
              hasSetupGuide={mod.hasSetupGuide}
              toolsCount={mod.toolsCount}
              canUninstall={mod.canUninstall}
              busy={busyMod === mod.id}
              error={actionErrors[mod.id] || null}
              onToggle={handleToggle}
            />
          ))}

          {/* Add Module card */}
          <button
            onClick={() => setShowInstall(true)}
            className="border border-dashed border-border p-4 flex items-center justify-center text-muted-foreground/30 hover:text-muted-foreground/60 hover:border-text-muted/30 transition-colors min-h-[88px]"
          >
            <span className="font-mono text-[10px] tracking-wider">+ ADD MODULE</span>
          </button>
        </div>

        {filteredMods.length === 0 && (
          <div className="border border-border mt-4 p-6 text-center">
            <span className="font-mono text-[9px] text-muted-foreground/40 tracking-widest">
              NO MODULES MATCH
            </span>
          </div>
        )}

        {/* Install modal */}
        {showInstall && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowInstall(false)}>
            <div className="bg-card border border-border p-6 max-w-md w-full mx-4" onClick={(e) => e.stopPropagation()}>
              <h2 className="font-mono text-[10px] tracking-widest text-muted-foreground/60 mb-4">INSTALL MODULE</h2>
              <input
                type="text"
                placeholder="github:user/repo"
                value={installSource}
                onChange={(e) => setInstallSource(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleInstall()}
                className="w-full bg-input border border-border px-3 py-2 font-mono text-[10px] text-foreground focus:border-primary/50 outline-none mb-3"
                autoFocus
              />
              <p className="font-mono text-[8px] text-muted-foreground/30 mb-4">
                Install a module from a GitHub repository. Example: github:username/anima-mod-example
              </p>
              {installError && (
                <p className="font-mono text-[8px] text-destructive mb-3">{installError}</p>
              )}
              <div className="flex gap-2 justify-end">
                <button
                  onClick={() => setShowInstall(false)}
                  className="font-mono text-[9px] text-muted-foreground/40 px-3 py-1 hover:text-foreground transition-colors"
                >
                  CANCEL
                </button>
                <button
                  onClick={handleInstall}
                  disabled={installing || !installSource.trim()}
                  className="font-mono text-[9px] text-primary border border-primary/30 px-3 py-1 hover:bg-primary/10 transition-colors disabled:opacity-40"
                >
                  {installing ? "INSTALLING..." : "INSTALL"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
