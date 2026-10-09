type ReviewSafety = {
  backupVerified: boolean;
  observedAtMs: number;
  expiresInSeconds: number;
  expiresAtMs?: number;
  parameters?: { action: string };
  inventory: {
    blockers: string[];
    resumeAuthorized?: boolean | null;
    cancelAuthorized?: boolean | null;
    operation?: { kind?: string | null; expectedInventoryDigest?: string | null; sourceHeadCatalogHash?: string | null; sourceHeadGeneration?: number | null } | null;
  };
};

function inventoryAllowsConfirmation(review: ReviewSafety): boolean {
  const action = review.parameters?.action;
  if (action !== "resume" && action !== "cancel") return review.inventory.blockers.length === 0;
  const inventory = review.inventory;
  const operation = inventory.operation;
  const authorized = action === "resume" ? inventory.resumeAuthorized : inventory.cancelAuthorized;
  return authorized === true && !!operation && typeof operation.kind === "string" && ["prune", "purge", "reanchor"].includes(operation.kind)
    && typeof operation.expectedInventoryDigest === "string" && /^[0-9a-f]{64}$/.test(operation.expectedInventoryDigest)
    && typeof operation.sourceHeadCatalogHash === "string" && /^[0-9a-f]{64}$/.test(operation.sourceHeadCatalogHash)
    && typeof operation.sourceHeadGeneration === "number" && Number.isSafeInteger(operation.sourceHeadGeneration) && operation.sourceHeadGeneration > 0
    && inventory.blockers.every((blocker) => ["maintenance_operation_pending", "head_lineage_corrupt"].includes(blocker));
}

export function canConfirmMaintenance(review: ReviewSafety | null, confirmed: boolean, nowMs: number): boolean {
  return !!review && confirmed && review.backupVerified && inventoryAllowsConfirmation(review)
    && nowMs >= review.observedAtMs && nowMs < (review.expiresAtMs ?? review.observedAtMs + review.expiresInSeconds * 1000);
}

export function maintenanceProgress(operation: { state: string; quarantinedCount: number; sweptCount: number; totalCount: number } | null): string {
  if (!operation) return "No maintenance operation is active.";
  return `${operation.state.replace(/_/g, " ")}: ${operation.sweptCount} of ${operation.totalCount} removed; ${operation.quarantinedCount} staged for removal.`;
}
