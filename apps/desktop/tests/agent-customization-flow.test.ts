import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const desktopRoot = join(import.meta.dir, "..");

function readSource(path: string): string {
  return readFileSync(join(desktopRoot, path), "utf8");
}

describe("agent customization flow chrome", () => {
  test("does not globally hide React Flow handles", () => {
    const css = readSource("src/index.css");
    const agentPage = readSource("src/pages/agent-customization/AgentCustomization.tsx");

    expect(css).not.toMatch(/^\s*\.react-flow__handle\s*\{\s*display:\s*none\s*!important;/m);
    expect(css).toMatch(/^\s*\.dashboard-flow\s+\.react-flow__handle\s*\{/m);
    expect(agentPage).toMatch(/className="(?:agent-customization-flow size-full|size-full agent-customization-flow)"/);
  });

  test("connects all setting inputs to the biography preview node", () => {
    const edges = readSource("src/pages/agent-customization/nodes/index.ts");
    const nodes = readSource("src/pages/agent-customization/hooks/useAgentNodes.ts");
    const nodeTypes = readSource("src/pages/agent-customization/nodes/index.ts");
    const previewNode = readSource("src/pages/agent-customization/nodes/AgentPreviewNode.tsx");

    expect(nodes).toContain('id: "avatar"');
    expect(nodes).toContain('id: "agentName"');
    expect(nodes).toContain('id: "relationship"');
    expect(nodes).not.toContain('id: "agentType"');
    expect(nodes).toContain('id: "preview"');
    expect(nodeTypes).toContain("agentPreview");
    expect(previewNode).toContain("Biography");
    expect(previewNode).toContain("identityDraft");
    expect(previewNode).toContain("personaDraft");
    expect(edges).not.toContain('target: "avatar"');
    expect(edges.match(/target:\s*"preview"/g)?.length ?? 0).toBe(12);
    expect(edges).toContain('source: "identity"');
    expect(edges).toContain('source: "persona"');
    expect(edges).toContain('source: "birthday"');
    expect(edges).toContain('source: "avatar"');
    expect(edges).toContain('source: "agentName"');
    expect(edges).toContain('source: "relationship"');
    expect(edges).not.toContain('source: "agentType"');
    expect(edges).toContain('source: "origin"');
    expect(edges).toContain('source: "revision"');
    expect(edges).toContain('source: "directive"');
    expect(edges).toContain('source: "autonomy"');
    expect(edges).toContain('source: "growth"');
    expect(edges).toContain('source: "intentions"');
  });

  test("loads compiled backend biography preview for the preview node", () => {
    const hook = readSource("src/pages/agent-customization/hooks/useAgentNodes.ts");
    const types = readSource("src/pages/agent-customization/nodes/types.ts");
    const previewNode = readSource("src/pages/agent-customization/nodes/AgentPreviewNode.tsx");

    expect(hook).toContain("getAgentBiographyPreview");
    expect(hook).toContain("biographyPreview");
    expect(hook).toContain("previewSections");
    expect(types).toContain("previewSections");
    expect(previewNode).toContain("previewSections");
    expect(previewNode).not.toContain("Context");
  });

  test("allows agent birthday override behind identity override", () => {
    const hook = readSource("src/pages/agent-customization/hooks/useAgentNodes.ts");
    const types = readSource("src/pages/agent-customization/nodes/types.ts");
    const birthdayNode = readSource("src/pages/agent-customization/nodes/AgentBirthdayNode.tsx");
    const previewNode = readSource("src/pages/agent-customization/nodes/AgentPreviewNode.tsx");

    expect(hook).toContain("agentBirthday");
    expect(hook).toContain("agentBirthdayDraft");
    expect(hook).toContain("onAgentBirthdaySave");
    expect(hook).toContain("biographyPreview?.agentBirthday");
    expect(hook).toContain("allowIdentityOverride: identityOverrideAllowed");
    expect(hook).not.toContain("api.users.update(user.id, { birthday");
    expect(hook).not.toContain("setBirthdayDraft(user?.birthday");
    expect(types).toContain("agentBirthday: string");
    expect(types).toContain("identityOverrideAllowed");
    expect(types).toContain("onAgentBirthdaySave");
    expect(birthdayNode).toContain('title="Agent Birthday"');
    expect(birthdayNode).toContain('type="datetime-local"');
    expect(birthdayNode).toContain("cannot be changed after setup unless override is enabled");
    expect(previewNode).toContain("formatAgentBirthday");
  });

  test("adds optional backend-backed profile nodes without requiring them by default", () => {
    const agentPage = readSource("src/pages/agent-customization/AgentCustomization.tsx");
    const hook = readSource("src/pages/agent-customization/hooks/useAgentNodes.ts");
    const previewNode = readSource("src/pages/agent-customization/nodes/AgentPreviewNode.tsx");

    expect(agentPage).toContain("optionalNodeToggles");
    expect(agentPage).toContain("OPTIONAL");
    expect(hook).toContain("optionalNodeVisibility");
    expect(hook).toContain("onOptionalNodeToggle");
    expect(hook).toContain('directive: false');
    expect(hook).toContain('origin: false');
    expect(hook).toContain('revision: false');
    expect(hook).toContain('autonomy: false');
    expect(hook).toContain('growth: false');
    expect(hook).toContain('intentions: false');
    expect(hook).toContain('id: "directive"');
    expect(hook).toContain('id: "origin"');
    expect(hook).toContain('id: "revision"');
    expect(hook).toContain('id: "autonomy"');
    expect(hook).toContain('id: "growth"');
    expect(hook).toContain('id: "intentions"');
    expect(hook).toContain('getSelfModelSection(user.id, "user_directive")');
    expect(hook).toContain('getSelfModelSection(user.id, "soul")');
    expect(hook).toContain('getSelfModelSection(user.id, "growth_log")');
    expect(hook).toContain('getSelfModelSection(user.id, "intentions")');
    expect(previewNode).toContain("Agent Directive");
    expect(previewNode).toContain("Origin Story");
    expect(previewNode).toContain("Self-Revision Inbox");
    expect(previewNode).toContain("Autonomy Policy");
    expect(previewNode).toContain("Active Intentions");
  });

  test("shows optional controls as a sidebar rail with obvious toggle state", () => {
    const agentPage = readSource("src/pages/agent-customization/AgentCustomization.tsx");
    const hook = readSource("src/pages/agent-customization/hooks/useAgentNodes.ts");

    expect(agentPage).toContain("agent-optional-rail");
    expect(agentPage).toContain("aria-pressed={toggle.active}");
    expect(agentPage).toContain("data-dangerous={toggle.dangerous}");
    expect(agentPage).toContain("title={toggle.dangerous ? \"Requires identity override to edit\" : \"Read-only profile history\"}");
    expect(agentPage).not.toContain("absolute left-4 top-4 z-10 flex items-center");
    expect(hook).toContain("position: { x: 540, y: 620 }");
    expect(hook).toContain("position: { x: 540, y: 780 }");
    expect(hook).toContain("position: { x: 540, y: 940 }");
    expect(hook).toContain("position: { x: 540, y: 1100 }");
    expect(hook).toContain("position: { x: 540, y: 1260 }");
    expect(hook).toContain("position: { x: 540, y: 1420 }");
  });

  test("does not let optional section failures blank core identity and persona", () => {
    const hook = readSource("src/pages/agent-customization/hooks/useAgentNodes.ts");

    expect(hook).toContain("loadOptionalSelfModelSections");
    expect(hook).toContain("Promise.allSettled");
    expect(hook).toContain("setSelfModelDrafts((current) => ({");
    expect(hook).not.toContain('Promise.all([\n      api.consciousness.getSelfModelSection(user.id, "identity"),\n      api.consciousness.getSelfModelSection(user.id, "persona"),\n      api.consciousness.getSelfModelSection(user.id, "user_directive")');
  });

  test("marks dangerous optional nodes as override gated and growth as read-only", () => {
    const hook = readSource("src/pages/agent-customization/hooks/useAgentNodes.ts");
    const textNode = readSource("src/pages/agent-customization/nodes/AgentTextNode.tsx");
    const types = readSource("src/pages/agent-customization/nodes/types.ts");

    expect(hook).toContain("requiresIdentityOverride");
    expect(hook).toContain("Enable identity override first");
    expect(hook).toContain("allowIdentityOverride: identityOverrideAllowed");
    expect(hook).toContain("override rewrites a protected agent profile field");
    expect(hook).toContain("readOnly: true");
    expect(types).toContain("requiresOverride");
    expect(types).toContain("readOnly");
    expect(textNode).toContain("This node cannot be changed unless override is enabled.");
    expect(textNode).toContain("checked={identityOverrideAllowed}");
    expect(textNode).toContain("readOnly");
  });

  test("lets the agent name node update the agent name behind override", () => {
    const hook = readSource("src/pages/agent-customization/hooks/useAgentNodes.ts");
    const types = readSource("src/pages/agent-customization/nodes/types.ts");
    const nameNode = readSource("src/pages/agent-customization/nodes/AgentNameNode.tsx");

    expect(hook).toContain("agentNameDraft");
    expect(hook).toContain("identityOverrideAllowed");
    expect(hook).toContain("onAgentNameSave");
    expect(hook).toContain("allowIdentityOverride: identityOverrideAllowed");
    expect(hook).toContain("dispatchAgentProfileChanged");
    expect(types).toContain("identityOverrideAllowed");
    expect(types).toContain("onIdentityOverrideAllowedChange");
    expect(types).toContain("onAgentNameChange");
    expect(types).toContain("onAgentNameSave");
    expect(nameNode).toContain("cannot be changed after setup unless override is enabled");
    expect(nameNode).toContain("supersedes the previous name memory");
    expect(nameNode).toContain("value={agentNameDraft}");
    expect(nameNode).toContain("checked={identityOverrideAllowed}");
    expect(nameNode).toContain("onClick: onAgentNameSave");
  });

  test("has a relationship node that updates the profile relationship", () => {
    const edges = readSource("src/pages/agent-customization/nodes/index.ts");
    const hook = readSource("src/pages/agent-customization/hooks/useAgentNodes.ts");
    const types = readSource("src/pages/agent-customization/nodes/types.ts");
    const relationshipNode = readSource("src/pages/agent-customization/nodes/AgentRelationshipNode.tsx");

    expect(edges).toContain("AgentRelationshipNode");
    expect(edges).toContain("agentRelationship");
    expect(hook).toContain("relationshipDraft");
    expect(hook).toContain("onRelationshipSave");
    expect(hook).toContain("allowIdentityOverride: identityOverrideAllowed");
    expect(types).toContain("RelationshipNode");
    expect(types).toContain("identityOverrideAllowed");
    expect(relationshipNode).toContain("cannot be changed after setup unless override is enabled");
    expect(relationshipNode).toContain("supersedes the previous relationship memory");
    expect(relationshipNode).toContain("value={relationshipDraft}");
    expect(relationshipNode).toContain("checked={identityOverrideAllowed}");
    expect(relationshipNode).toContain("onClick: onRelationshipSave");
  });

  test("does not expose agent type as an editable or visible agent setting", () => {
    const edges = readSource("src/pages/agent-customization/nodes/index.ts");
    const hook = readSource("src/pages/agent-customization/hooks/useAgentNodes.ts");
    const types = readSource("src/pages/agent-customization/nodes/types.ts");
    const previewNode = readSource("src/pages/agent-customization/nodes/AgentPreviewNode.tsx");

    expect(edges).not.toContain("AgentTypeNode");
    expect(edges).not.toContain("agentType");
    expect(hook).not.toContain("agentTypeDraft");
    expect(hook).not.toContain("onAgentTypeSave");
    expect(types).not.toContain("AgentTypeNode");
    expect(types).not.toContain("onAgentTypeSave");
    expect(previewNode).not.toContain("agentType");
  });

  test("uses a custom solid edge with a moving pulse orb for setting links", () => {
    const edges = readSource("src/pages/agent-customization/nodes/index.ts");
    const edgeComponent = readSource("src/pages/agent-customization/nodes/AgentPulseEdge.tsx");
    const agentPage = readSource("src/pages/agent-customization/AgentCustomization.tsx");
    const css = readSource("src/index.css");

    expect(edges).toContain("edgeTypes");
    expect(edges.match(/type:\s*"agentPulse"/g)?.length ?? 0).toBe(12);
    expect(edges).not.toMatch(/animated:\s*true/);
    expect(agentPage).toContain("edgeTypes={edgeTypes}");
    expect(edgeComponent).toContain("BaseEdge");
    expect(edgeComponent).toContain("getBezierPath");
    expect(edgeComponent).toContain("<animateMotion");
    expect(edgeComponent).toContain("agent-pulse-orb");
    expect(css).toContain(".agent-customization-flow .agent-pulse-orb");
    expect(css).toContain("@media (prefers-reduced-motion: reduce)");
  });
});
