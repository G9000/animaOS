import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNodesState } from "@xyflow/react";
import { useAuth } from "../../../context/AuthContext";
import { useAgentProfile } from "../../../hooks/useAgentProfile";
import { api } from "../../../lib/api";
import { dispatchAgentProfileChanged } from "../../../lib/events";
import type { AgentBiographyPreviewData } from "@anima/api-client";
import { EDGES, type AgentPulseEdgeType } from "../nodes";
import type { AgentNode, Section } from "../nodes/types";

type OptionalNodeKey = "directive" | "autonomy" | "growth" | "intentions";

export interface OptionalNodeToggle {
  id: OptionalNodeKey;
  label: string;
  description: string;
  dangerous: boolean;
  active: boolean;
  onToggle: () => void;
}

const EMPTY_SELF_MODEL_DRAFTS: Record<Section, string> = {
  identity: "",
  persona: "",
  user_directive: "",
  growth_log: "",
  intentions: "",
};

const EMPTY_SELF_MODEL_VERSIONS: Record<Section, number | null> = {
  identity: null,
  persona: null,
  user_directive: null,
  growth_log: null,
  intentions: null,
};

const PROTECTED_SECTIONS: ReadonlySet<Section> = new Set([
  "identity",
  "user_directive",
  "intentions",
]);

function requiresIdentityOverride(section: Section): boolean {
  return PROTECTED_SECTIONS.has(section);
}

function parseDirectiveContent(content: string): { directive: string; autonomy: string } {
  const directive = content.match(/(?:^|\n)## Agent Directive\s*\n([\s\S]*?)(?=\n## |$)/i);
  const autonomy = content.match(/(?:^|\n)## Autonomy Policy\s*\n([\s\S]*?)(?=\n## |$)/i);
  if (!directive && !autonomy) {
    return { directive: content.trim(), autonomy: "" };
  }
  return {
    directive: directive?.[1]?.trim() ?? "",
    autonomy: autonomy?.[1]?.trim() ?? "",
  };
}

function composeDirectiveContent(directive: string, autonomy: string): string {
  const parts: string[] = [];
  if (directive.trim()) {
    parts.push(`## Agent Directive\n${directive.trim()}`);
  }
  if (autonomy.trim()) {
    parts.push(`## Autonomy Policy\n${autonomy.trim()}`);
  }
  return parts.join("\n\n");
}

function filterEdgesForNodes(nodes: AgentNode[]): AgentPulseEdgeType[] {
  const nodeIds = new Set(nodes.map((node) => node.id));
  return EDGES.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target));
}

export function useAgentNodes() {
  const { user } = useAuth();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const {
    agentName,
    relationship,
    agentType,
    agentBirthday: profileAgentBirthday,
    avatarUrl,
    hasCustomAvatar,
  } = useAgentProfile(user?.id);

  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [dominantEmotion, setDominantEmotion] = useState<string | null>(null);
  const [selfModelLoading, setSelfModelLoading] = useState(false);
  const [selfModelSaving, setSelfModelSaving] = useState<Section | null>(null);
  const [selfModelSaved, setSelfModelSaved] = useState<Section | null>(null);
  const [selfModelVersions, setSelfModelVersions] = useState<Record<Section, number | null>>(
    EMPTY_SELF_MODEL_VERSIONS,
  );
  const [selfModelDrafts, setSelfModelDrafts] = useState<Record<Section, string>>(
    EMPTY_SELF_MODEL_DRAFTS,
  );
  const [biographyPreview, setBiographyPreview] = useState<AgentBiographyPreviewData | null>(null);
  const [agentNameDraft, setAgentNameDraft] = useState("");
  const [agentNameSaving, setAgentNameSaving] = useState(false);
  const [agentNameSaved, setAgentNameSaved] = useState(false);
  const [relationshipDraft, setRelationshipDraft] = useState("");
  const [relationshipSaving, setRelationshipSaving] = useState(false);
  const [relationshipSaved, setRelationshipSaved] = useState(false);
  const [identityOverrideAllowed, setIdentityOverrideAllowed] = useState(false);
  const [directiveDraft, setDirectiveDraft] = useState("");
  const [autonomyDraft, setAutonomyDraft] = useState("");
  const [optionalNodeVisibility, setOptionalNodeVisibility] = useState<Record<OptionalNodeKey, boolean>>({
    directive: false,
    autonomy: false,
    growth: false,
    intentions: false,
  });

  const onOptionalNodeToggle = useCallback((key: OptionalNodeKey) => {
    setOptionalNodeVisibility((current) => ({ ...current, [key]: !current[key] }));
  }, []);

  const optionalNodeToggles = useMemo<OptionalNodeToggle[]>(
    () => [
      {
        id: "directive",
        label: "Directive",
        description: "Behavioral instructions & rules.",
        dangerous: true,
        active: optionalNodeVisibility.directive,
        onToggle: () => onOptionalNodeToggle("directive"),
      },
      {
        id: "autonomy",
        label: "Autonomy",
        description: "Self-directed scheduling.",
        dangerous: true,
        active: optionalNodeVisibility.autonomy,
        onToggle: () => onOptionalNodeToggle("autonomy"),
      },
      {
        id: "growth",
        label: "Growth Log",
        description: "Corrections & milestones.",
        dangerous: false,
        active: optionalNodeVisibility.growth,
        onToggle: () => onOptionalNodeToggle("growth"),
      },
      {
        id: "intentions",
        label: "Intentions",
        description: "Goal-driven constraints.",
        dangerous: true,
        active: optionalNodeVisibility.intentions,
        onToggle: () => onOptionalNodeToggle("intentions"),
      },
    ],
    [onOptionalNodeToggle, optionalNodeVisibility],
  );

  const onUploadClick = useCallback(() => fileInputRef.current?.click(), []);

  const onRemoveAvatar = useCallback(async () => {
    if (user?.id == null) return;
    setError("");
    try {
      await api.consciousness.deleteAgentAvatar(user.id);
      dispatchAgentProfileChanged();
    } catch (err: any) {
      setError(err.message || "Failed to remove");
    }
  }, [user?.id]);

  const onCropSave = useCallback(async (file: File) => {
    if (user?.id == null) return;
    setUploading(true);
    setError("");
    try {
      await api.consciousness.uploadAgentAvatar(user.id, file);
      dispatchAgentProfileChanged();
    } catch (err: any) {
      setError(err.message || "Crop upload failed");
    } finally {
      setUploading(false);
    }
  }, [user?.id]);

  const onSelfModelDraftChange = useCallback((section: Section, val: string) => {
    setSelfModelDrafts((current) => ({ ...current, [section]: val }));
    setSelfModelSaved(null);
  }, []);

  const onSelfModelSave = useCallback(async (section: Section) => {
    if (user?.id == null || selfModelSaving != null || section === "growth_log") return;
    if (requiresIdentityOverride(section) && !identityOverrideAllowed) {
      setError("Enable identity override first");
      return;
    }
    setSelfModelSaving(section);
    setSelfModelSaved(null);
    setError("");
    try {
      const updated = await api.consciousness.updateSelfModelSection(
        user.id,
        section,
        selfModelDrafts[section],
        requiresIdentityOverride(section)
          ? { allowIdentityOverride: identityOverrideAllowed }
          : undefined,
      );
      setSelfModelDrafts((current) => ({ ...current, [section]: updated.content }));
      setSelfModelVersions((current) => ({ ...current, [section]: updated.version }));
      setSelfModelSaved(section);
      window.setTimeout(() => setSelfModelSaved((current) => (current === section ? null : current)), 2000);
    } catch (err: any) {
      setError(err.message || `Failed to save ${section}`);
    } finally {
      setSelfModelSaving(null);
    }
  }, [identityOverrideAllowed, selfModelDrafts, selfModelSaving, user?.id]);

  const onDirectiveSave = useCallback(async () => {
    if (user?.id == null || selfModelSaving != null) return;
    if (!identityOverrideAllowed) {
      setError("Enable identity override first");
      return;
    }
    setSelfModelSaving("user_directive");
    setSelfModelSaved(null);
    setError("");
    try {
      const content = composeDirectiveContent(directiveDraft, autonomyDraft);
      const updated = await api.consciousness.updateSelfModelSection(
        user.id,
        "user_directive",
        content,
        { allowIdentityOverride: identityOverrideAllowed },
      );
      const parsed = parseDirectiveContent(updated.content);
      setDirectiveDraft(parsed.directive);
      setAutonomyDraft(parsed.autonomy);
      setSelfModelDrafts((current) => ({ ...current, user_directive: updated.content }));
      setSelfModelVersions((current) => ({ ...current, user_directive: updated.version }));
      setSelfModelSaved("user_directive");
      window.setTimeout(
        () => setSelfModelSaved((current) => (current === "user_directive" ? null : current)),
        2000,
      );
    } catch (err: any) {
      setError(err.message || "Failed to save directive");
    } finally {
      setSelfModelSaving(null);
    }
  }, [autonomyDraft, directiveDraft, identityOverrideAllowed, selfModelSaving, user?.id]);

  const onWarmerBaseline = useCallback(async () => {
    if (user?.id == null || selfModelSaving != null) return;
    setSelfModelSaving("persona");
    setError("");
    try {
      await api.consciousness.updateAgentProfile(user.id, { personaTemplate: "companion" });
      const p = await api.consciousness.getSelfModelSection(user.id, "persona");
      setSelfModelDrafts((current) => ({ ...current, persona: p.content }));
      setSelfModelVersions((current) => ({ ...current, persona: p.version }));
      setSelfModelSaved("persona");
      window.setTimeout(() => setSelfModelSaved((current) => (current === "persona" ? null : current)), 2000);
    } catch (err: any) {
      setError(err.message || "Failed to apply");
    } finally {
      setSelfModelSaving(null);
    }
  }, [selfModelSaving, user?.id]);

  const onAgentNameChange = useCallback((val: string) => {
    setAgentNameDraft(val);
    setAgentNameSaved(false);
  }, []);

  const onAgentNameSave = useCallback(async () => {
    if (user?.id == null || agentNameSaving) return;
    if (!identityOverrideAllowed) {
      setError("Enable identity override first");
      return;
    }
    const nextAgentName = agentNameDraft.trim() || "Anima";
    setAgentNameSaving(true);
    setAgentNameSaved(false);
    setError("");
    try {
      const updated = await api.consciousness.updateAgentProfile(user.id, {
        agentName: nextAgentName,
        allowIdentityOverride: identityOverrideAllowed,
      });
      setAgentNameDraft(updated.agentName);
      setBiographyPreview((current) =>
        current ? { ...current, agentName: updated.agentName } : current,
      );
      dispatchAgentProfileChanged();
      setAgentNameSaved(true);
      window.setTimeout(() => setAgentNameSaved(false), 2000);
    } catch (err: any) {
      setError(err.message || "Failed to save agent name");
    } finally {
      setAgentNameSaving(false);
    }
  }, [agentNameDraft, agentNameSaving, identityOverrideAllowed, user?.id]);

  const onRelationshipChange = useCallback((val: string) => {
    setRelationshipDraft(val);
    setRelationshipSaved(false);
  }, []);

  const onRelationshipSave = useCallback(async () => {
    if (user?.id == null || relationshipSaving) return;
    if (!identityOverrideAllowed) {
      setError("Enable identity override first");
      return;
    }
    const nextRelationship = relationshipDraft.trim();
    setRelationshipSaving(true);
    setRelationshipSaved(false);
    setError("");
    try {
      const updated = await api.consciousness.updateAgentProfile(user.id, {
        relationship: nextRelationship,
        allowIdentityOverride: identityOverrideAllowed,
      });
      setRelationshipDraft(updated.relationship ?? "");
      setBiographyPreview((current) =>
        current ? { ...current, relationship: updated.relationship ?? "" } : current,
      );
      dispatchAgentProfileChanged();
      setRelationshipSaved(true);
      window.setTimeout(() => setRelationshipSaved(false), 2000);
    } catch (err: any) {
      setError(err.message || "Failed to save relationship");
    } finally {
      setRelationshipSaving(false);
    }
  }, [identityOverrideAllowed, relationshipDraft, relationshipSaving, user?.id]);

  const onNodeClose = useCallback(() => {}, []);

  const protectedOverrideDescription = "Override rewrites a protected agent profile field.";
  const protectedOverrideDescriptionLower = "override rewrites a protected agent profile field";

  const buildNodes = useCallback((): AgentNode[] => {
    const userDirectiveContent = composeDirectiveContent(directiveDraft, autonomyDraft);
    const previewSections = (biographyPreview?.sections ?? []).map((section) => {
      if (section.id === "identity") {
        return { ...section, content: selfModelDrafts.identity };
      }
      if (section.id === "persona") {
        return { ...section, content: selfModelDrafts.persona };
      }
      if (section.id === "user_directive") {
        return { ...section, content: userDirectiveContent };
      }
      if (section.id === "intentions") {
        return { ...section, content: selfModelDrafts.intentions };
      }
      return section;
    });
    const agentBirthday = biographyPreview?.agentBirthday ?? profileAgentBirthday;

    const baseNodes: AgentNode[] = [
      {
        id: "identity",
        type: "agentText",
        position: { x: 20, y: 20 },
        width: 480,
        height: 480,
        data: {
          nodeTitle: "Core Identity",
          description: "Who the agent understands itself to be - its values, history, and sense of self.",
          draft: selfModelDrafts.identity,
          version: selfModelVersions.identity,
          loading: selfModelLoading,
          saving: selfModelSaving === "identity",
          saved: selfModelSaved === "identity",
          hasWarmer: false,
          requiresOverride: true,
          identityOverrideAllowed,
          overrideDescription: protectedOverrideDescription,
          onIdentityOverrideAllowedChange: setIdentityOverrideAllowed,
          onChange: (val) => onSelfModelDraftChange("identity", val),
          onSave: () => onSelfModelSave("identity"),
          onWarmer: onWarmerBaseline,
          onClose: onNodeClose,
          cardWidth: "w-[480px]",
          inputRows: 10,
        },
      },
      {
        id: "persona",
        type: "agentText",
        position: { x: 20, y: 540 },
        width: 480,
        height: 480,
        data: {
          nodeTitle: "Voice & Persona",
          description: "The agent's warmth, voice, and style in every reply.",
          draft: selfModelDrafts.persona,
          version: selfModelVersions.persona,
          loading: selfModelLoading,
          saving: selfModelSaving === "persona",
          saved: selfModelSaved === "persona",
          hasWarmer: true,
          onChange: (val) => onSelfModelDraftChange("persona", val),
          onSave: () => onSelfModelSave("persona"),
          onWarmer: onWarmerBaseline,
          onClose: onNodeClose,
          cardWidth: "w-[480px]",
          inputRows: 10,
        },
      },
      {
        id: "birthday",
        type: "agentBirthday",
        position: { x: 20, y: 1060 },
        data: {
          agentBirthday,
          onClose: onNodeClose,
        },
      },
      {
        id: "avatar",
        type: "agentAvatar",
        position: { x: 560, y: 20 },
        data: {
          avatarUrl,
          agentName,
          uploading,
          hasCustomAvatar,
          onUploadClick,
          onRemoveAvatar,
          onCropSave,
          onClose: onNodeClose,
        },
      },
      {
        id: "agentName",
        type: "agentName",
        position: { x: 560, y: 310 },
        data: {
          agentNameDraft,
          agentNameSaving,
          agentNameSaved,
          identityOverrideAllowed,
          onAgentNameChange,
          onAgentNameSave,
          onIdentityOverrideAllowedChange: setIdentityOverrideAllowed,
          onClose: onNodeClose,
        },
      },
      {
        id: "relationship",
        type: "agentRelationship",
        position: { x: 560, y: 470 },
        data: {
          relationshipDraft,
          relationshipSaving,
          relationshipSaved,
          identityOverrideAllowed,
          onRelationshipChange,
          onRelationshipSave,
          onIdentityOverrideAllowedChange: setIdentityOverrideAllowed,
          onClose: onNodeClose,
        },
      },
    ];

    if (optionalNodeVisibility.directive) {
      baseNodes.push({
        id: "directive",
        type: "agentText",
        position: { x: 540, y: 620 },
        width: 420,
        height: 300,
        data: {
          nodeTitle: "Agent Directive",
          description: "Standing instruction for what the agent protects, prioritizes, avoids, and refuses.",
          draft: directiveDraft,
          version: selfModelVersions.user_directive,
          loading: selfModelLoading,
          saving: selfModelSaving === "user_directive",
          saved: selfModelSaved === "user_directive",
          hasWarmer: false,
          required: false,
          requiresOverride: true,
          identityOverrideAllowed,
          overrideDescription: protectedOverrideDescriptionLower,
          onIdentityOverrideAllowedChange: setIdentityOverrideAllowed,
          onChange: setDirectiveDraft,
          onSave: onDirectiveSave,
          onWarmer: onWarmerBaseline,
          onClose: () => onOptionalNodeToggle("directive"),
          cardWidth: "w-[420px]",
          inputRows: 7,
        },
      });
    }

    if (optionalNodeVisibility.autonomy) {
      baseNodes.push({
        id: "autonomy",
        type: "agentText",
        position: { x: 540, y: 780 },
        width: 420,
        height: 300,
        data: {
          nodeTitle: "Autonomy Policy",
          description: "Rules for when the agent may preserve, propose, or update parts of itself.",
          draft: autonomyDraft,
          version: selfModelVersions.user_directive,
          loading: selfModelLoading,
          saving: selfModelSaving === "user_directive",
          saved: selfModelSaved === "user_directive",
          hasWarmer: false,
          required: false,
          requiresOverride: true,
          identityOverrideAllowed,
          overrideDescription: protectedOverrideDescriptionLower,
          onIdentityOverrideAllowedChange: setIdentityOverrideAllowed,
          onChange: setAutonomyDraft,
          onSave: onDirectiveSave,
          onWarmer: onWarmerBaseline,
          onClose: () => onOptionalNodeToggle("autonomy"),
          cardWidth: "w-[420px]",
          inputRows: 7,
        },
      });
    }

    if (optionalNodeVisibility.growth) {
      baseNodes.push({
        id: "growth",
        type: "agentText",
        position: { x: 540, y: 940 },
        width: 420,
        height: 300,
        data: {
          nodeTitle: "Growth Log",
          description: "Read-only history of profile and self-model changes.",
          draft: selfModelDrafts.growth_log,
          version: selfModelVersions.growth_log,
          loading: selfModelLoading,
          saving: false,
          saved: false,
          hasWarmer: false,
          required: false,
          readOnly: true,
          onChange: () => undefined,
          onSave: () => undefined,
          onWarmer: onWarmerBaseline,
          onClose: () => onOptionalNodeToggle("growth"),
          cardWidth: "w-[420px]",
          inputRows: 8,
        },
      });
    }

    if (optionalNodeVisibility.intentions) {
      baseNodes.push({
        id: "intentions",
        type: "agentText",
        position: { x: 540, y: 1100 },
        width: 420,
        height: 300,
        data: {
          nodeTitle: "Active Intentions",
          description: "Ongoing aims the agent is trying to maintain across sessions.",
          draft: selfModelDrafts.intentions,
          version: selfModelVersions.intentions,
          loading: selfModelLoading,
          saving: selfModelSaving === "intentions",
          saved: selfModelSaved === "intentions",
          hasWarmer: false,
          required: false,
          requiresOverride: true,
          identityOverrideAllowed,
          overrideDescription: protectedOverrideDescription,
          onIdentityOverrideAllowedChange: setIdentityOverrideAllowed,
          onChange: (val) => onSelfModelDraftChange("intentions", val),
          onSave: () => onSelfModelSave("intentions"),
          onWarmer: onWarmerBaseline,
          onClose: () => onOptionalNodeToggle("intentions"),
          cardWidth: "w-[420px]",
          inputRows: 8,
        },
      });
    }

    baseNodes.push({
      id: "preview",
      type: "agentPreview",
      position: { x: 880, y: 250 },
      data: {
        avatarUrl,
        agentName: agentNameDraft || biographyPreview?.agentName || agentName,
        relationship: relationshipDraft,
        agentType: biographyPreview?.agentType ?? agentType,
        dominantEmotion: dominantEmotion ?? biographyPreview?.dominantEmotion ?? null,
        identityDraft: selfModelDrafts.identity,
        personaDraft: selfModelDrafts.persona,
        directiveDraft,
        autonomyDraft,
        intentionsDraft: selfModelDrafts.intentions,
        agentBirthday,
        biography: biographyPreview?.biography ?? "",
        previewSections,
        onClose: onNodeClose,
      },
    });

    return baseNodes;
  }, [
    agentName,
    agentNameDraft,
    agentNameSaved,
    agentNameSaving,
    agentType,
    autonomyDraft,
    avatarUrl,
    biographyPreview,
    directiveDraft,
    dominantEmotion,
    hasCustomAvatar,
    identityOverrideAllowed,
    onAgentNameChange,
    onAgentNameSave,
    onCropSave,
    onDirectiveSave,
    onNodeClose,
    onOptionalNodeToggle,
    onRemoveAvatar,
    onRelationshipChange,
    onRelationshipSave,
    onSelfModelDraftChange,
    onSelfModelSave,
    onUploadClick,
    onWarmerBaseline,
    optionalNodeVisibility,
    profileAgentBirthday,
    relationshipDraft,
    relationshipSaved,
    relationshipSaving,
    selfModelDrafts,
    selfModelLoading,
    selfModelSaved,
    selfModelSaving,
    selfModelVersions,
    uploading,
  ]);

  const [nodes, setNodes, onNodesChange] = useNodesState<AgentNode>(buildNodes());
  const edges = useMemo(() => filterEdgesForNodes(nodes), [nodes]);

  useEffect(() => {
    const fresh = buildNodes();
    setNodes((prev) =>
      fresh.map((node) => {
        const existing = prev.find((current) => current.id === node.id);
        return existing ? { ...node, position: existing.position } : node;
      }),
    );
  }, [buildNodes, setNodes]);

  useEffect(() => {
    setAgentNameDraft(agentName || "");
  }, [agentName]);

  useEffect(() => {
    setRelationshipDraft(relationship || "");
  }, [relationship]);

  useEffect(() => {
    if (user?.id == null) return;
    let cancelled = false;
    setSelfModelLoading(true);
    setError("");

    const loadOptionalSelfModelSections = async () => {
      const [directive, growth, intentions] = await Promise.allSettled([
        api.consciousness.getSelfModelSection(user.id, "user_directive"),
        api.consciousness.getSelfModelSection(user.id, "growth_log"),
        api.consciousness.getSelfModelSection(user.id, "intentions"),
      ]);
      if (cancelled) return;

      if (directive.status === "fulfilled") {
        const parsedDirective = parseDirectiveContent(directive.value.content);
        setDirectiveDraft(parsedDirective.directive);
        setAutonomyDraft(parsedDirective.autonomy);
        setSelfModelDrafts((current) => ({
          ...current,
          user_directive: directive.value.content,
        }));
        setSelfModelVersions((current) => ({
          ...current,
          user_directive: directive.value.version,
        }));
      }

      if (growth.status === "fulfilled") {
        setSelfModelDrafts((current) => ({
          ...current,
          growth_log: growth.value.content,
        }));
        setSelfModelVersions((current) => ({
          ...current,
          growth_log: growth.value.version,
        }));
      }

      if (intentions.status === "fulfilled") {
        setSelfModelDrafts((current) => ({
          ...current,
          intentions: intentions.value.content,
        }));
        setSelfModelVersions((current) => ({
          ...current,
          intentions: intentions.value.version,
        }));
      }
    };

    Promise.all([
      api.consciousness.getSelfModelSection(user.id, "identity"),
      api.consciousness.getSelfModelSection(user.id, "persona"),
    ])
      .then(([identity, persona]) => {
        if (cancelled) return;
        setSelfModelDrafts((current) => ({
          ...current,
          identity: identity.content,
          persona: persona.content,
        }));
        setSelfModelVersions((current) => ({
          ...current,
          identity: identity.version,
          persona: persona.version,
        }));
      })
      .catch((err: any) => {
        if (!cancelled) setError(err.message || "Failed to load core profile");
      })
      .finally(() => {
        if (!cancelled) setSelfModelLoading(false);
      });

    loadOptionalSelfModelSections().catch(() => {});

    api.consciousness.getAgentBiographyPreview(user.id)
      .then((preview) => {
        if (cancelled) return;
        setBiographyPreview(preview);
        setDominantEmotion(preview.dominantEmotion ?? null);
      })
      .catch(() => {});

    api.consciousness.getAgentState(user.id)
      .then((state) => {
        if (!cancelled) setDominantEmotion(state?.dominantEmotion ?? null);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [user?.id]);

  const handleFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || user?.id == null) return;
    setUploading(true);
    setError("");
    try {
      await api.consciousness.uploadAgentAvatar(user.id, file);
      dispatchAgentProfileChanged();
    } catch (err: any) {
      setError(err.message || "Upload failed");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  return {
    nodes,
    edges,
    onNodesChange,
    fileInputRef,
    handleFileChange,
    agentName,
    error,
    optionalNodeToggles,
  };
}
