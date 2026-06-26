export const AUTH_PATH_PREFIX = "/api/auth";

export const AUTH_ROUTES = {
  CREATE_AI_CHAT: "/api/auth/create-ai/chat",
  REGISTER: "/api/auth/register",
  LOGIN: "/api/auth/login",
  ME: "/api/auth/me",
  LOGOUT: "/api/auth/logout",
  CHANGE_PASSWORD: "/api/auth/change-password",
  RECOVER: "/api/auth/recover",
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
}

export interface ChangePasswordResponse {
  success: boolean;
  unlockToken: string;
}

export interface RegisterResponse extends AuthUser {
  unlockToken: string;
  recoveryPhrase?: string;
}

export interface RecoverRequest {
  recoveryPhrase: string;
  newPassword: string;
}

export interface RecoverResponse extends AuthUser {
  unlockToken: string;
  message: string;
}

export interface LogoutResponse {
  success: boolean;
}

export interface UserResponse extends AuthUser {}
