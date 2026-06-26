export const DAEMON_API_VERSION = "v1" as const;

export const DAEMON_CONTROL_PREFIX = "/api/v1/runtime-daemon" as const;

export const DAEMON_ROUTES = {
  STATUS: `${DAEMON_CONTROL_PREFIX}/status`,
  START: `${DAEMON_CONTROL_PREFIX}/start`,
  STOP: `${DAEMON_CONTROL_PREFIX}/stop`,
  RESTART: `${DAEMON_CONTROL_PREFIX}/restart`,
  OPEN_LOGS: `${DAEMON_CONTROL_PREFIX}/logs`,
  LOCK: `${DAEMON_CONTROL_PREFIX}/lock`,
  UNLOCK: `${DAEMON_CONTROL_PREFIX}/unlock`,
} as const;

export type DaemonState = "stopped" | "starting" | "ready" | "degraded" | "locked" | "stopping" | "failed";

export type DaemonCommand = "status" | "start" | "stop" | "restart" | "open_logs" | "lock" | "unlock";

export type DaemonErrorCategory = "transient" | "dependency" | "permission" | "policy" | "validation" | "internal";

export interface DaemonLocalAuthPolicy {
  bindHost: "localhost-only" | "loopback-only" | "all-interfaces";
  trustedClients: "token-only" | "token-or-os-account" | "os-account-only";
  tokenRequirement: "required" | "optional" | "none";
  tokenRotationWindowSeconds: number;
}

export interface DaemonRetryPolicy {
  maxAttempts: number;
  baseDelayMs: number;
  maxDelayMs: number;
  jitterPercent: number;
  retryableCodes: readonly string[];
}

export interface DaemonRuntimeInfo {
  pid?: number;
  processPath?: string;
  startedAt?: string;
  exitedAt?: string;
  exitCode?: number | null;
  exitReason?: string | null;
  commandLine?: string;
  port?: number;
  version?: string;
}

export interface DaemonStatusPayload {
  state: DaemonState;
  uptimeSeconds?: number;
  lastReadyAt?: string | null;
  lastHealthCheckAt?: string | null;
  healthSummary?: string | null;
  runtime: DaemonRuntimeInfo;
  authPolicy: DaemonLocalAuthPolicy;
}

export interface DaemonLockPayload {
  isLocked: boolean;
  lockedBy?: "ui" | "policy" | "admin" | "unknown";
  lockedReason?: string | null;
  unlockAfterSeconds?: number | null;
  lockAffectsBackgroundJobs: boolean;
  lockEffectiveAt?: string;
}

export interface DaemonControlError {
  code: string;
  category: DaemonErrorCategory;
  message: string;
  hint?: string | null;
  retryable: boolean;
  retryAfterSeconds?: number;
}

export interface DaemonHealthResponse {
  status: DaemonStatusPayload;
  lockState: DaemonLockPayload;
  error?: DaemonControlError | null;
}

export interface DaemonStartRequest {
  source?: "ui" | "cli" | "service";
  backgroundMode?: boolean;
  preferReadyWithinMs?: number;
}

export interface DaemonStopRequest {
  force?: boolean;
  timeoutMs?: number;
}

export interface DaemonRestartRequest {
  force?: boolean;
  backgroundMode?: boolean;
}

export interface DaemonLockRequest {
  source?: "user" | "policy";
  reason?: string;
  pauseBackgroundJobs?: boolean;
}

export interface DaemonUnlockRequest {
  source?: "user" | "handoff";
  handoffToken?: string;
}

export interface DaemonOpenLogsRequest {
  tailLines?: number;
  follow?: boolean;
}

export interface DaemonCommandResponse {
  command: DaemonCommand;
  accepted: boolean;
  state: DaemonState;
  message?: string | null;
  status?: DaemonHealthResponse;
}

export interface DaemonOpenLogsResponse {
  command: "open_logs";
  accepted: boolean;
  path: string;
  tailLines?: number;
  output?: string | null;
}

export interface DaemonClientIdentity {
  clientId: string;
  clientKind: "desktop" | "cli" | "tooling";
  issuedAt: string;
}
