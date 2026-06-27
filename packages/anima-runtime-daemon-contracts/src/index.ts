export const DAEMON_API_VERSION = "v1";
export const DAEMON_CONTROL_PREFIX = `/v1`;

export const DAEMON_ROUTES = {
  status: `${DAEMON_CONTROL_PREFIX}/status`,
  control: `${DAEMON_CONTROL_PREFIX}/control`,
  logs: `${DAEMON_CONTROL_PREFIX}/logs`,
  nonce: `${DAEMON_CONTROL_PREFIX}/nonce`,
  health: `${DAEMON_CONTROL_PREFIX}/health`,
} as const;

export const DAEMON_CONTROL_TOKEN_HEADER = "x-anima-daemon-token";
export const DAEMON_CONTROL_TOKEN_ENV = "ANIMA_DAEMON_CONTROL_TOKEN";

export type DaemonState =
  | "stopped"
  | "starting"
  | "ready"
  | "degraded"
  | "locked"
  | "stopping"
  | "failed";

export type DaemonCommand =
  | "start"
  | "stop"
  | "restart"
  | "lock"
  | "unlock"
  | "set-background";

export type DaemonErrorCategory =
  | "auth"
  | "state"
  | "runtime"
  | "validation"
  | "internal";

export interface DaemonRuntimeIdentity {
  readonly command: string;
  readonly args: readonly string[];
  readonly workingDir: string | null;
}

export interface DaemonRuntimeStatus {
  readonly pid: number | null;
  readonly port: number;
  readonly portFile: string;
  readonly pidFile: string;
  readonly logFile: string;
  readonly artifactPath: string;
  readonly launchMode: "python" | "artifact" | "command";
}

export interface DaemonLockStatus {
  readonly enabled: boolean;
  readonly lockOnClose: boolean;
  readonly lockOnIdle: boolean;
}

export interface DaemonRestartPolicy {
  readonly enabled: boolean;
  readonly maxAttempts: number;
  readonly attempts: number;
  readonly nextDelaySeconds: number;
}

export interface DaemonStatusResponse {
  readonly version: string;
  readonly state: DaemonState;
  readonly runtimeIdentity: DaemonRuntimeIdentity;
  readonly runtime: DaemonRuntimeStatus;
  readonly lock: DaemonLockStatus;
  readonly restart: DaemonRestartPolicy;
  readonly backgroundEnabled: boolean;
  readonly updatedAt: string;
  readonly error: string | null;
}

export interface DaemonHealthResponse {
  readonly version: string;
  readonly status: DaemonState;
  readonly updatedAt?: string;
  readonly updated_at?: string;
  readonly controlToken?: string;
}

export interface DaemonRuntimeNonceResponse {
  readonly runtimeNonce: string;
}

export interface DaemonControlRequest {
  readonly backgroundEnabled?: boolean;
}

export interface DaemonControlResponse {
  readonly success: boolean;
  readonly message: string;
  readonly state: DaemonState;
}

export interface DaemonErrorResponse {
  readonly category: DaemonErrorCategory;
  readonly message: string;
  readonly detail?: string;
}

export interface DaemonLogResponse {
  readonly logFile: string;
  readonly lines: readonly string[];
  readonly requestedLines: number;
  readonly truncated: boolean;
}
