import type { Node } from "@xyflow/react";

// ── Node data interfaces ──────────────────────────────────────────────────────

export interface PreviewData extends Record<string, unknown> {
  avatarUrl: string;
  agentName: string;
  uploading: boolean;
  hasCustomAvatar: boolean;
  onUploadClick: () => void;
  onRemoveAvatar: () => void;
  onCropSave: (file: File) => void;
  onClose: () => void;
}

export interface AgentNameData extends Record<string, unknown> {
  agentNameDraft: string;
  agentNameSaving: boolean;
  agentNameSaved: boolean;
  identityOverrideAllowed: boolean;
  onAgentNameChange: (val: string) => void;
  onAgentNameSave: () => void;
  onIdentityOverrideAllowedChange: (val: boolean) => void;
  onClose: () => void;
}

export interface BiographyPreviewSection {
  id: string;
  title: string;
  content: string;
  source: string;
}

export interface BiographyPreviewData extends Record<string, unknown> {
  avatarUrl: string;
  agentName: string;
  relationship?: string | null;
  dominantEmotion?: string | null;
  identityDraft: string;
  personaDraft: string;
  originDraft: string;
  directiveDraft: string;
  autonomyDraft: string;
  revisionDraft: string;
  intentionsDraft: string;
  agentBirthday: string;
  biography: string;
  previewSections: BiographyPreviewSection[];
  onClose: () => void;
}

export interface TextData extends Record<string, unknown> {
  nodeTitle: string;
  description: string;
  draft: string;
  version: number | null;
  loading: boolean;
  saving: boolean;
  saved: boolean;
  hasWarmer: boolean;
  readOnly?: boolean;
  required?: boolean;
  requiresOverride?: boolean;
  identityOverrideAllowed?: boolean;
  overrideDescription?: string;
  onChange: (val: string) => void;
  onSave: () => void;
  onWarmer: () => void;
  onIdentityOverrideAllowedChange?: (val: boolean) => void;
  onClose: () => void;
  cardWidth?: string;
  inputRows?: number;
}

export interface BirthdayData extends Record<string, unknown> {
  agentBirthday: string;
  agentBirthdayDraft: string;
  agentBirthdaySaving: boolean;
  agentBirthdaySaved: boolean;
  identityOverrideAllowed: boolean;
  onAgentBirthdayChange: (val: string) => void;
  onAgentBirthdaySave: () => void;
  onIdentityOverrideAllowedChange: (val: boolean) => void;
  onClose: () => void;
}

export interface RelationshipData extends Record<string, unknown> {
  relationshipDraft: string;
  relationshipSaving: boolean;
  relationshipSaved: boolean;
  identityOverrideAllowed: boolean;
  onRelationshipChange: (val: string) => void;
  onRelationshipSave: () => void;
  onIdentityOverrideAllowedChange: (val: boolean) => void;
  onClose: () => void;
}

export type AvatarNode        = Node<PreviewData,         "agentAvatar">;
export type AgentNameNode     = Node<AgentNameData,        "agentName">;
export type TextNode          = Node<TextData,             "agentText">;
export type BirthdayNode      = Node<BirthdayData,         "agentBirthday">;
export type RelationshipNode  = Node<RelationshipData,     "agentRelationship">;
export type BiographyPreviewNode = Node<BiographyPreviewData, "agentPreview">;
export type AgentNode = AvatarNode | AgentNameNode | TextNode | BirthdayNode | RelationshipNode | BiographyPreviewNode;

export type Section = "identity" | "soul" | "persona" | "user_directive" | "growth_log" | "intentions";

// ── Shared textarea class ─────────────────────────────────────────────────────

export const TA = [
  "nodrag nowheel w-full resize-y",
  "bg-secondary/40 border border-border/40",
  "px-3 py-2.5 font-mono text-[11px] leading-relaxed text-foreground/75",
  "outline-none focus:border-border transition-colors",
  "placeholder:text-muted-foreground/30 disabled:opacity-40",
].join(" ");
