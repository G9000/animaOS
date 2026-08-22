import type { CoreFsClientScope } from "@anima/api-client";

const SCOPE_ORDER: Record<CoreFsClientScope, number> = {
  none: 0,
  read: 1,
  write: 2,
  manage: 3,
};

export function requiresGrantConfirmation(
  previous: CoreFsClientScope,
  next: CoreFsClientScope,
): boolean {
  return SCOPE_ORDER[next] > SCOPE_ORDER[previous];
}

export function clientScopeDescription(scope: CoreFsClientScope): string {
  if (scope === "read") return "Read content and discover descendants.";
  if (scope === "write") return "Read, create, and edit content.";
  if (scope === "manage") return "Write plus rename, move, trash, and restore.";
  return "No access.";
}
