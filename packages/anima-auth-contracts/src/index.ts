export const AUTH_PATH_PREFIX = "/api/auth";

export const AUTH_ROUTES = {
  CREATE_AI_CHAT: "/api/auth/create-ai/chat",
  REGISTER: "/api/auth/register",
  LOGIN: "/api/auth/login",
  ME: "/api/auth/me",
  LOGOUT: "/api/auth/logout",
  CHANGE_PASSWORD: "/api/auth/change-password",
  RECOVER: "/api/auth/recover",
  PREPARE_RECOVERY_CREDENTIAL: "/api/auth/recovery-credential/prepare",
  CONFIRM_RECOVERY_CREDENTIAL: "/api/auth/recovery-credential/confirm",
  CORE_FS_CHANGE_PASSWORD: "/api/auth/corefs/change-password",
  CORE_FS_PREPARE_RECOVERY_CREDENTIAL: "/api/auth/corefs/recovery-credential/prepare",
  CORE_FS_CONFIRM_RECOVERY_CREDENTIAL: "/api/auth/corefs/recovery-credential/confirm",
} as const;

export type UserGender = string | null;

export interface AuthUser {
  id: number;
  username: string;
  name: string;
  gender?: UserGender;
  age?: number | null;
  birthday?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface AuthRequestBase {
  username: string;
}

export interface RegisterRequest extends AuthRequestBase {
  password: string;
  name: string;
  agentName: string;
  userDirective: string;
  relationship: string;
  personaTemplate: string;
  agentType: "companion" | "mirror";
}

export interface CreateAiChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface CreateAiChatRequest {
  messages: CreateAiChatMessage[];
  ownerName: string;
}

export interface CreateAiChatResponse {
  message: string;
  done: boolean;
  soulData?: Record<string, string> | null;
}

export interface LoginRequest extends AuthRequestBase {
  password: string;
}

export interface LoginResponse extends AuthUser {
  unlockToken: string;
  message: string;
}

export interface ChangePasswordRequest {
  oldPassword: string;
  newPassword: string;
  scope: RecoveryCredentialScope;
}

export interface ChangePasswordResponse {
  success: boolean;
  unlockToken: string;
}

export interface CorefsChangePasswordRequest {
  currentPassword: string;
  newPassword: string;
}

export interface CorefsCredentialResponse {
  success: boolean;
  scope: "fs";
}

export interface PrepareCorefsRecoveryCredentialRequest {
  currentRecoveryPhrase: string;
  currentPassword: string;
}

export interface PrepareCorefsRecoveryCredentialResponse {
  success: boolean;
  recoveryPhrase: string;
  pendingGeneration: number;
  scope: "fs";
}

export interface ConfirmCorefsRecoveryCredentialRequest {
  recoveryPhrase: string;
  pendingGeneration: number;
}

export type RecoveryCredentialScope = "full" | "soul" | "fs";

export interface PrepareRecoveryCredentialRequest {
  currentRecoveryPhrase: string;
  currentPassword: string;
  scope: RecoveryCredentialScope;
}

export interface PrepareRecoveryCredentialResponse {
  success: boolean;
  recoveryPhrase: string;
  pendingGeneration: number;
  scope: RecoveryCredentialScope;
}

export interface ConfirmRecoveryCredentialRequest {
  recoveryPhrase: string;
  pendingGeneration: number;
  scope: RecoveryCredentialScope;
}

export interface ConfirmRecoveryCredentialResponse {
  success: boolean;
}

export interface RegisterResponse extends AuthUser {
  unlockToken: string;
  recoveryPhrase?: string;
}

export interface RecoverRequest {
  recoveryPhrase: string;
  newPassword: string;
  scope: RecoveryCredentialScope;
}

export interface RecoverResponse extends AuthUser {
  unlockToken: string;
  message: string;
}

export interface LogoutResponse {
  success: boolean;
}

export interface UserResponse extends AuthUser {}
