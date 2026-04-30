import { useParams, useNavigate } from "react-router-dom";
import { useModDetail, getModClient } from "../lib/mod-client";
import StatusBadge from "../components/mods/StatusBadge";
import ConfigForm from "../components/mods/ConfigForm";
import SetupWizard from "../components/mods/SetupWizard";
import { useState } from "react";

function formatEventDetail(detail: Record<string, unknown> | null | undefined): string {
  if (!detail || Object.keys(detail).length === 0) return "";
  return JSON.stringify(detail);
}

export default function ModDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { mod, loading, error, refresh } = useModDetail(id!);
  const [actionPending, setActionPending] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmUninstall, setConfirmUninstall] = useState(false);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <span className="font-mono text-[10px] text-muted-foreground/40 tracking-widest">
          LOADING...
        </span>
      </div>
    );
  }

  if (error || !mod) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <span className="font-mono text-[10px] text-destructive">{error || "Module not found"}</span>
        <button
          onClick={() => navigate("/mods")}
          className="font-mono text-[9px] text-muted-foreground/40 hover:text-foreground"
        >
          BACK TO MODULES
        </button>
      </div>
    );
  }

  const needsSetup = mod.setupGuide &&
    mod.setupGuide.length > 0 &&
    (!mod.config || Object.keys(mod.config).length === 0);

  const handleSaveConfig = async (values: Record<string, unknown>) => {
    const client = getModClient();
    await client.updateModConfig(id!, values);
    await refresh();
  };

  const handleHealthCheck = async (): Promise<boolean> => {
    try {
      const client = getModClient();
      const data = await client.getModHealth(id!);
      return data.status === "running";
    } catch {
      return false;
    }
  };

  const handleAction = async (action: "enable" | "disable" | "restart") => {
    setActionPending(action);
    setActionError(null);
    try {
      const client = getModClient();
      if (action === "enable") await client.enableMod(id!);
      else if (action === "disable") await client.disableMod(id!);
      else await client.restartMod(id!);
      await refresh();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setActionPending(null);
    }
  };

  const handleUninstall = async () => {
    if (!confirmUninstall) {
      setConfirmUninstall(true);
      return;
    }

    setActionPending("uninstall");
    setActionError(null);
    try {
      await getModClient().uninstallMod(id!);
      navigate("/mods");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Uninstall failed");
    } finally {
      setActionPending(null);
    }
  };

  return (
    <div className="h-full overflow-auto p-6">
      <div className="max-w-3xl mx-auto">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button
            onClick={() => navigate("/mods")}
            className="font-mono text-[10px] text-muted-foreground/40 hover:text-foreground"
            title="Back to modules"
          >
            &larr;
          </button>
          <h1 className="font-mono text-[11px] tracking-widest uppercase text-foreground">
            {mod.id}
          </h1>
          <StatusBadge status={mod.status} />
          <span className="font-mono text-[8px] text-muted-foreground/30 ml-auto">
            v{mod.version}
          </span>
          <button
            onClick={() => void refresh()}
            className="font-mono text-[8px] text-muted-foreground/40 border border-border px-2 py-1 hover:text-foreground hover:border-text-muted/30 transition-colors"
            title="Refresh module"
          >
            REFRESH
          </button>
        </div>

        {/* Setup Wizard (first-time) */}
        {needsSetup && mod.configSchema && mod.setupGuide ? (
          <SetupWizard
            steps={mod.setupGuide}
            schema={mod.configSchema}
            onComplete={handleSaveConfig}
            onHealthCheck={handleHealthCheck}
          />
        ) : (
          <div className="space-y-6">
            {/* Status Section */}
            <div className="border border-border p-4">
              <div className="flex items-center justify-between gap-3 mb-3">
                <div className="font-mono text-[9px] tracking-widest text-muted-foreground/60">
                  STATUS
                </div>
                <div className="flex flex-wrap gap-1">
                  {mod.toolsCount ? (
                    <span className="font-mono text-[7px] tracking-widest text-muted-foreground/40 border border-border px-1.5 py-0.5">
                      TOOLS {mod.toolsCount}
                    </span>
                  ) : null}
                  {mod.canUninstall && (
                    <span className="font-mono text-[7px] tracking-widest text-muted-foreground/40 border border-border px-1.5 py-0.5">
                      USER MOD
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-4">
                <StatusBadge status={mod.status} />
                {mod.health?.uptime && (
                  <span className="font-mono text-[8px] text-muted-foreground/30">
                    since {new Date(mod.health.uptime).toLocaleString()}
                  </span>
                )}
              </div>
              {mod.health?.lastError && (
                <p className="font-mono text-[8px] text-destructive mt-2">
                  {mod.health.lastError}
                </p>
              )}
              <div className="flex gap-2 mt-3">
                {mod.enabled ? (
                  <>
                    <button
                      onClick={() => handleAction("restart")}
                      disabled={actionPending !== null}
                      className="font-mono text-[8px] text-muted-foreground/40 border border-border px-2 py-0.5 hover:text-foreground hover:border-text-muted/30 transition-colors disabled:opacity-40"
                    >
                      {actionPending === "restart" ? "RESTARTING..." : "RESTART"}
                    </button>
                    <button
                      onClick={() => handleAction("disable")}
                      disabled={actionPending !== null}
                      className="font-mono text-[8px] text-destructive/60 border border-border px-2 py-0.5 hover:text-destructive hover:border-destructive/30 transition-colors disabled:opacity-40"
                    >
                      {actionPending === "disable" ? "DISABLING..." : "DISABLE"}
                    </button>
                  </>
                ) : (
                  <button
                    onClick={() => handleAction("enable")}
                    disabled={actionPending !== null}
                    className="font-mono text-[8px] text-primary border border-primary/30 px-2 py-0.5 hover:bg-primary/10 transition-colors disabled:opacity-40"
                  >
                    {actionPending === "enable" ? "ENABLING..." : "ENABLE"}
                  </button>
                )}
              </div>
              {mod.canUninstall && (
                <div className="flex gap-2 mt-3">
                  <button
                    onClick={handleUninstall}
                    disabled={actionPending === "uninstall"}
                    className="font-mono text-[8px] text-destructive/70 border border-destructive/30 px-2 py-0.5 hover:text-destructive hover:bg-destructive/10 transition-colors disabled:opacity-40"
                  >
                    {confirmUninstall
                      ? actionPending === "uninstall"
                        ? "UNINSTALLING..."
                        : "CONFIRM UNINSTALL"
                      : "UNINSTALL"}
                  </button>
                  {confirmUninstall && (
                    <button
                      onClick={() => setConfirmUninstall(false)}
                      className="font-mono text-[8px] text-muted-foreground/40 border border-border px-2 py-0.5 hover:text-foreground hover:border-text-muted/30 transition-colors"
                    >
                      CANCEL
                    </button>
                  )}
                </div>
              )}
              {actionError && (
                <p className="font-mono text-[8px] text-destructive mt-2">
                  {actionError}
                </p>
              )}
            </div>

            {/* Config Form */}
            {mod.configSchema && (
              <div className="border border-border p-4">
                <div className="font-mono text-[9px] tracking-widest text-muted-foreground/60 mb-3">
                  CONFIGURATION
                </div>
                <ConfigForm
                  schema={mod.configSchema}
                  values={mod.config ?? {}}
                  onSave={handleSaveConfig}
                />
              </div>
            )}

            <div className="border border-border p-4">
              <div className="font-mono text-[9px] tracking-widest text-muted-foreground/60 mb-3">
                EVENTS
              </div>
              {mod.events && mod.events.length > 0 ? (
                <div className="space-y-2">
                  {mod.events.map((event) => (
                    <div key={event.id} className="border border-border/60 p-2">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-[8px] tracking-widest text-foreground uppercase">
                          {event.eventType}
                        </span>
                        {event.createdAt && (
                          <span className="font-mono text-[7px] text-muted-foreground/30 ml-auto">
                            {new Date(event.createdAt).toLocaleString()}
                          </span>
                        )}
                      </div>
                      {formatEventDetail(event.detail) && (
                        <p className="font-mono text-[8px] text-muted-foreground/40 mt-1 break-all">
                          {formatEventDetail(event.detail)}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <span className="font-mono text-[8px] text-muted-foreground/30">
                  No recent events.
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
