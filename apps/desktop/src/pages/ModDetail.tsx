import { useParams, useNavigate } from "react-router-dom";
import { useModDetail, getModClient } from "../lib/mod-client";
import StatusBadge from "../components/mods/StatusBadge";
import ConfigForm from "../components/mods/ConfigForm";
import SetupWizard from "../components/mods/SetupWizard";

export default function ModDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { mod, loading, error, refresh } = useModDetail(id!);

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <span className="font-mono text-[10px] text-text-muted/40 tracking-widest">
          LOADING...
        </span>
      </div>
    );
  }

  if (error || !mod) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <span className="font-mono text-[10px] text-danger">{error || "Module not found"}</span>
        <button
          onClick={() => navigate("/mods")}
          className="font-mono text-[9px] text-text-muted/40 hover:text-text"
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
    await client.api.mods({ id: id! }).config.put(values);
    refresh();
  };

  const handleHealthCheck = async (): Promise<boolean> => {
    try {
      const client = getModClient();
      const { data } = await client.api.mods({ id: id! }).health.get();
      return data?.status === "running";
    } catch {
      return false;
    }
  };

  const handleAction = async (action: "enable" | "disable" | "restart") => {
    const client = getModClient();
    if (action === "enable") await client.api.mods({ id: id! }).enable.post();
    else if (action === "disable") await client.api.mods({ id: id! }).disable.post();
    else await client.api.mods({ id: id! }).restart.post();
    refresh();
  };

  return (
    <div className="h-full overflow-auto p-6">
      <div className="max-w-xl mx-auto">
        {/* Header */}
        <div className="flex items-center gap-3 mb-6">
          <button
            onClick={() => navigate("/mods")}
            className="font-mono text-[10px] text-text-muted/40 hover:text-text"
          >
            &larr;
          </button>
          <h1 className="font-mono text-[11px] tracking-widest uppercase text-text">
            {mod.id}
          </h1>
          <StatusBadge status={mod.status} />
          <span className="font-mono text-[8px] text-text-muted/30 ml-auto">
            v{mod.version}
          </span>
        </div>

        {/* Setup Wizard (first-time) */}
        {needsSetup && mod.configSchema && mod.setupGuide ? (
          <SetupWizard
            steps={mod.setupGuide}
            schema={mod.configSchema}
            modId={id!}
            onComplete={handleSaveConfig}
            onHealthCheck={handleHealthCheck}
          />
        ) : (
          <div className="space-y-6">
            {/* Status Section */}
            <div className="border border-border p-4">
              <div className="font-mono text-[9px] tracking-widest text-text-muted/60 mb-3">
                STATUS
              </div>
              <div className="flex items-center gap-4">
                <StatusBadge status={mod.status} />
                {mod.health?.uptime && (
                  <span className="font-mono text-[8px] text-text-muted/30">
                    since {new Date(mod.health.uptime).toLocaleString()}
                  </span>
                )}
              </div>
              {mod.health?.lastError && (
                <p className="font-mono text-[8px] text-danger mt-2">
                  {mod.health.lastError}
                </p>
              )}
              <div className="flex gap-2 mt-3">
                {mod.enabled ? (
                  <>
                    <button
                      onClick={() => handleAction("restart")}
                      className="font-mono text-[8px] text-text-muted/40 border border-border px-2 py-0.5 hover:text-text hover:border-text-muted/30 transition-colors"
                    >
                      RESTART
                    </button>
                    <button
                      onClick={() => handleAction("disable")}
                      className="font-mono text-[8px] text-danger/60 border border-border px-2 py-0.5 hover:text-danger hover:border-danger/30 transition-colors"
                    >
                      DISABLE
                    </button>
                  </>
                ) : (
                  <button
                    onClick={() => handleAction("enable")}
                    className="font-mono text-[8px] text-primary border border-primary/30 px-2 py-0.5 hover:bg-primary/10 transition-colors"
                  >
                    ENABLE
                  </button>
                )}
              </div>
            </div>

            {/* Config Form */}
            {mod.configSchema && (
              <div className="border border-border p-4">
                <div className="font-mono text-[9px] tracking-widest text-text-muted/60 mb-3">
                  CONFIGURATION
                </div>
                <ConfigForm
                  schema={mod.configSchema}
                  values={mod.config ?? {}}
                  onSave={handleSaveConfig}
                />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
