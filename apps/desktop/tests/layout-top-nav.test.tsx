import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import { TopNavAgentButton } from "../src/components/layout/LayoutTopNav";

describe("TopNavAgentButton", () => {
  test("shows the agent name and mood beside the avatar when expanded", () => {
    const html = renderToStaticMarkup(
      <TopNavAgentButton
        agentName="ANIMA"
        avatarUrl="/avatar.png"
        dominantEmotion="calm"
        stateThought="quietly present"
        expanded
        onClick={() => {}}
        onThoughtClick={() => {}}
      />,
    );

    expect(html).toContain('alt="ANIMA"');
    expect(html).toContain('title="ANIMA - calm - quietly present"');
    expect(html).toContain("ANIMA");
    expect(html).toContain(">calm<");
    expect(html).toContain(">quietly present<");
  });

  test("keeps the mood visible on the collapsed avatar", () => {
    const html = renderToStaticMarkup(
      <TopNavAgentButton
        agentName="ANIMA"
        avatarUrl="/avatar.png"
        dominantEmotion="calm"
        stateThought="quietly present"
        expanded={false}
        onClick={() => {}}
        onThoughtClick={() => {}}
      />,
    );

    expect(html).toContain('title="ANIMA - calm - quietly present"');
    expect(html).toContain("rounded-full");
    expect(html).not.toContain(">ANIMA<");
    expect(html).not.toContain(">calm<");
    expect(html).not.toContain(">quietly present<");
  });
});
