import { useEffect, useMemo, useState } from "react";
import type {
  CoreFsClientAccessState,
  CoreFsClientInstallation,
  CoreFsClientScope,
  CoreFsGrantFolder,
} from "@anima/api-client";
import { glass } from "@anima/standard-templates";
import { api } from "../../lib/api";
import {
  clientScopeDescription,
  requiresGrantConfirmation,
} from "../../lib/corefsAccess";

type PendingGrant = {
  installation: CoreFsClientInstallation;
  folder: CoreFsGrantFolder;
  previous: CoreFsClientScope;
  next: CoreFsClientScope;
};

const SELECT_CLASS = "bg-background border border-hairline px-2 py-1.5 font-mono text-label text-foreground outline-none focus:border-hairline-strong";

function currentScope(
  installation: CoreFsClientInstallation,
  folderStableId: string,
): CoreFsClientScope {
  return installation.grants.find((grant) => grant.folderStableId === folderStableId)?.scope ?? "none";
}

function auditTime(value: string | null): string {
  if (!value) return "never";
  const parsed = new Date(value);
  return Number.isNaN(parsed.valueOf()) ? "unavailable" : parsed.toLocaleString();
}

export default function CoreFSAccessSettings() {
  const [state, setState] = useState<CoreFsClientAccessState | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [confirmedPackages, setConfirmedPackages] = useState<Record<string, boolean>>({});
  const [pendingGrant, setPendingGrant] = useState<PendingGrant | null>(null);

  const activeInstallations = useMemo(
    () => state?.installations.filter((item) => item.status !== "revoked") ?? [],
    [state],
  );

  const refresh = async () => {
    setLoading(true);
    setError("");
    try {
      setState(await api.corefs.clientAccess());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Client access state is unavailable.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const approve = async (installation: CoreFsClientInstallation) => {
    if (!confirmedPackages[installation.installationId]) return;
    setBusy(installation.installationId);
    setError("");
    try {
      setState(await api.corefs.approveClient(installation.installationId));
      setConfirmedPackages((current) => ({ ...current, [installation.installationId]: false }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Package approval failed.");
    } finally {
      setBusy("");
    }
  };

  const applyGrant = async (grant: PendingGrant, confirmed: boolean) => {
    const operation = `${grant.installation.installationId}:${grant.folder.stableId}`;
    setBusy(operation);
    setError("");
    try {
      setState(
        await api.corefs.updateClientGrant(
          grant.installation.installationId,
          grant.folder.stableId,
          grant.next,
          confirmed,
        ),
      );
      setPendingGrant(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Grant update failed.");
    } finally {
      setBusy("");
    }
  };

  const chooseGrant = (
    installation: CoreFsClientInstallation,
    folder: CoreFsGrantFolder,
    next: CoreFsClientScope,
  ) => {
    const previous = currentScope(installation, folder.stableId);
    const grant = { installation, folder, previous, next };
    if (requiresGrantConfirmation(previous, next)) {
      setPendingGrant(grant);
    } else {
      void applyGrant(grant, false);
    }
  };

  const revoke = async (installation: CoreFsClientInstallation) => {
    const confirmed = window.confirm(
      `Revoke every CoreFS grant for ${installation.displayName} on this device?`,
    );
    if (!confirmed) return;
    setBusy(installation.installationId);
    setError("");
    try {
      setState(await api.corefs.revokeClient(installation.installationId));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Client revocation failed.");
    } finally {
      setBusy("");
    }
  };

  return (
    <div className={`${glass} p-6 space-y-5`}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="font-mono text-label tracking-caps-4 uppercase text-foreground/55">
            CoreFS Access
          </h1>
          <p className="mt-2 font-mono text-caption leading-relaxed tracking-wide text-foreground/35">
            Grants are device-local and bind an exact verified package digest to stable Core folder IDs.
            Moving this Core never transfers executable access.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="font-mono text-micro tracking-caps-3 uppercase text-foreground/35 hover:text-foreground/70 disabled:opacity-30"
        >
          {loading ? "Checking..." : "Refresh"}
        </button>
      </div>

      {state?.reapprovalRequiredAfterTransfer && (
        <div className="border border-warning/35 bg-warning/[0.06] p-3 font-mono text-caption text-warning/80">
          Client-authored folders arrived from another device. Install and verify the destination package,
          then approve its folder scopes here.
        </div>
      )}
      {error && <p className="font-mono text-caption text-destructive">{error}</p>}

      {!loading && activeInstallations.length === 0 && (
        <div className="border border-hairline-faint bg-foreground/[0.02] p-4">
          <p className="font-mono text-caption text-foreground/35">
            No platform-verified clients or mods are registered on this device.
          </p>
        </div>
      )}

      {activeInstallations.map((installation) => {
        const needsApproval = installation.status !== "approved"
          || installation.approvedDigest !== installation.installDigest;
        return (
          <section
            key={installation.installationId}
            className="border border-hairline-faint bg-foreground/[0.02] p-4 space-y-4"
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="font-mono text-sm text-foreground/80">
                  {installation.displayName} <span className="text-foreground/35">v{installation.packageVersion}</span>
                </h2>
                <p className="mt-1 font-mono text-micro tracking-wide text-foreground/35">
                  {installation.packageId} · {installation.clientId}
                </p>
              </div>
              <span className="font-mono text-micro uppercase tracking-caps-3 text-foreground/45">
                {installation.status.replace(/_/g, " ")}
              </span>
            </div>

            <dl className="grid gap-2 font-mono text-micro tracking-wide">
              <div><dt className="inline text-foreground/30">Publisher </dt><dd className="inline text-foreground/55">{installation.publisher ? `${installation.publisher.identity} (${installation.publisher.verified ? "verified" : "unverified"})` : "unsigned · exact digest only"}</dd></div>
              <div><dt className="inline text-foreground/30">SHA-256 </dt><dd className="inline break-all text-foreground/55">{installation.installDigest}</dd></div>
              <div><dt className="inline text-foreground/30">Last use </dt><dd className="inline text-foreground/55">{auditTime(installation.lastUsedAt)}</dd></div>
              <div><dt className="inline text-foreground/30">Declared roles </dt><dd className="inline text-foreground/55">{installation.declaredRoles.join(", ") || "none"}</dd></div>
            </dl>

            {installation.status === "collision" ? (
              <p className="font-mono text-caption text-destructive">
                Package ID or role namespace collision. Revoke the conflicting installation before approval.
              </p>
            ) : needsApproval ? (
              <div className="space-y-2 border-t border-hairline-faint pt-3">
                <label className="flex items-start gap-2 font-mono text-caption text-foreground/50">
                  <input
                    type="checkbox"
                    checked={Boolean(confirmedPackages[installation.installationId])}
                    onChange={(event) => setConfirmedPackages((current) => ({ ...current, [installation.installationId]: event.target.checked }))}
                  />
                  I verified this package ID, publisher, digest, and the scopes shown below.
                </label>
                <button
                  type="button"
                  disabled={!confirmedPackages[installation.installationId] || Boolean(busy)}
                  onClick={() => void approve(installation)}
                  className="border border-hairline px-3 py-1.5 font-mono text-micro uppercase tracking-caps-3 text-foreground/55 hover:bg-foreground/[0.05] disabled:opacity-30"
                >
                  Approve exact installation
                </button>
              </div>
            ) : (
              <div className="space-y-2 border-t border-hairline-faint pt-3">
                {state?.folders.map((folder) => {
                  const scope = currentScope(installation, folder.stableId);
                  return (
                    <div key={folder.stableId} className="grid grid-cols-[1fr_auto] gap-3 items-center">
                      <div>
                        <p className="font-mono text-caption text-foreground/60">{folder.path || "/"}</p>
                        <p className="font-mono text-micro text-foreground/25">
                          {folder.role ?? "custom folder"} · stable ID {folder.stableId} · {clientScopeDescription(scope)}
                        </p>
                      </div>
                      <select
                        aria-label={`${installation.displayName} access to ${folder.path}`}
                        className={SELECT_CLASS}
                        value={scope}
                        disabled={Boolean(busy)}
                        onChange={(event) => chooseGrant(installation, folder, event.target.value as CoreFsClientScope)}
                      >
                        <option value="none">None</option>
                        <option value="read">Read</option>
                        <option value="write">Write</option>
                        <option value="manage">Manage</option>
                      </select>
                    </div>
                  );
                })}
              </div>
            )}

            <button
              type="button"
              disabled={Boolean(busy)}
              onClick={() => void revoke(installation)}
              className="font-mono text-micro uppercase tracking-caps-3 text-destructive/70 hover:text-destructive disabled:opacity-30"
            >
              Revoke installation
            </button>
          </section>
        );
      })}

      {pendingGrant && (
        <div className="sticky bottom-4 border border-accent/35 bg-background/95 p-4 shadow-xl backdrop-blur space-y-3">
          <p className="font-mono text-caption text-foreground/65">
            Confirm expanding {pendingGrant.installation.displayName} from {pendingGrant.previous} to {pendingGrant.next}
            {" "}for {pendingGrant.folder.path}. This permits: {clientScopeDescription(pendingGrant.next)}
          </p>
          <div className="flex gap-2">
            <button type="button" onClick={() => void applyGrant(pendingGrant, true)} className="bg-accent px-3 py-1.5 font-mono text-micro uppercase tracking-caps-3 text-background">Confirm grant</button>
            <button type="button" onClick={() => setPendingGrant(null)} className="border border-hairline px-3 py-1.5 font-mono text-micro uppercase tracking-caps-3 text-foreground/50">Cancel</button>
          </div>
        </div>
      )}
    </div>
  );
}
