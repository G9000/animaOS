import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import { TodayContextPanel } from "../src/components/TodayContextPanel";

describe("TodayContextPanel", () => {
  test("renders a compact companion check-in by default", () => {
    const html = renderToStaticMarkup(
      <TodayContextPanel
        context={null}
        greeting="How are you arriving today?"
        onSave={() => {}}
        onClear={() => {}}
      />,
    );

    expect(html).toContain("Today");
    expect(html).toContain("How are you arriving today?");
    expect(html).toContain('aria-label="Set mood steady"');
    expect(html).toContain('aria-label="Set mood tired"');
    expect(html).toContain('aria-label="Set mood anxious"');
    expect(html).toContain('aria-label="Set mood energized"');
    expect(html).toContain('aria-label="Set energy low"');
    expect(html).toContain('aria-label="Show more today context controls"');
    expect(html).not.toContain('aria-label="Set mood overwhelmed"');
    expect(html).not.toContain('placeholder="note"');
  });

  test("renders full editor when expanded", () => {
    const html = renderToStaticMarkup(
      <TodayContextPanel
        context={null}
        defaultExpanded
        onSave={() => {}}
        onClear={() => {}}
      />,
    );

    expect(html).toContain('aria-label="Set mood overwhelmed"');
    expect(html).toContain('placeholder="mood"');
    expect(html).toContain('placeholder="energy"');
    expect(html).toContain('placeholder="note"');
  });

  test("summarizes active today context compactly", () => {
    const html = renderToStaticMarkup(
      <TodayContextPanel
        context={{
          date: "2026-05-31",
          mood: "tired",
          energy: "low",
        }}
        onSave={() => {}}
        onClear={() => {}}
      />,
    );

    expect(html).toContain("Today: tired · low energy");
    expect(html).not.toContain('placeholder="note"');
  });
});
