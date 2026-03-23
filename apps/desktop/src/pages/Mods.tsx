import { useMods, useModEvents, getModClient } from "../lib/mod-client";
import ModCard from "../components/mods/ModCard";
import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";

export default function Mods() {
  const { mods, loading, error, refresh } = useMods();
  const [showInstall, setShowInstall] = useState(false);
  const [installSource, setInstallSource] = useState("");
  const [installing, setInstalling] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const navigate = useNavigate();

  // Real-time status updates
  useModEvents(useCallback(() => {
    refresh();
  }, [refresh]));

  const handleToggle = async (id: string, enable: boolean) => {
    const client = getModClient();
    if (enable) {
      await client.api.mods({ id }).enable.post();
    } else {
      await client.api.mods({ id }).disable.post();
    }
    refresh();
  };

  const handleInstall = async () => {
    if (!installSource.trim()) return;
    setInstalling(true);
    setInstallError(null);
    try {
      const client = getModClient();
      const { data, error: err } = await client.api.mods.install.post({ source: installSource.trim() });
      if (err) throw new Error(String(err));
      setShowInstall(false);
      setInstallSource("");
      refresh();
      if (data?.id) navigate(`/mods/${data.id}`);
    } catch (e) {
      setInstallError(e instanceof Error ? e.message : "Install failed");
    } finally {
      setInstalling(false);
    }
  };

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <span className="font-mono text-[10px] text-text-muted/40 tracking-widest">
          LOADING MODULES...
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <span className="font-mono text-[10px] text-danger tracking-wider">
          ANIMA-MOD NOT RUNNING
        </span>
        <span className="font-mono text-[8px] text-text-muted/40">
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
      <div className="max-w-3xl mx-auto">
        <h1 className="font-mono text-[11px] tracking-widest text-text-muted/60 mb-6">
          MODULES
        </h1>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {mods.map((mod) => (
            <ModCard
              key={mod.id}
              id={mod.id}
              version={mod.version}
              status={mod.status}
              enabled={mod.enabled}
              hasConfigSchema={mod.hasConfigSchema}
              onToggle={handleToggle}
            />
          ))}

          {/* Add Module card */}
          <button
            onClick={() => setShowInstall(true)}
            className="border border-dashed border-border p-4 flex items-center justify-center text-text-muted/30 hover:text-text-muted/60 hover:border-text-muted/30 transition-colors min-h-[88px]"
          >
            <span className="font-mono text-[10px] tracking-wider">+ ADD MODULE</span>
          </button>
        </div>

        {/* Install modal */}
        {showInstall && (
          <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50" onClick={() => setShowInstall(false)}>
            <div className="bg-bg-card border border-border p-6 max-w-md w-full mx-4" onClick={(e) => e.stopPropagation()}>
              <h2 className="font-mono text-[10px] tracking-widest text-text-muted/60 mb-4">INSTALL MODULE</h2>
              <input
                type="text"
                placeholder="github:user/repo"
                value={installSource}
                onChange={(e) => setInstallSource(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleInstall()}
                className="w-full bg-bg-input border border-border px-3 py-2 font-mono text-[10px] text-text focus:border-primary/50 outline-none mb-3"
                autoFocus
              />
              <p className="font-mono text-[8px] text-text-muted/30 mb-4">
                Install a module from a GitHub repository. Example: github:username/anima-mod-example
              </p>
              {installError && (
                <p className="font-mono text-[8px] text-danger mb-3">{installError}</p>
              )}
              <div className="flex gap-2 justify-end">
                <button
                  onClick={() => setShowInstall(false)}
                  className="font-mono text-[9px] text-text-muted/40 px-3 py-1 hover:text-text transition-colors"
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
