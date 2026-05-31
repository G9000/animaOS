import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import { TodayContextPanel } from "../src/components/TodayContextPanel";

describe("TodayContextPanel", () => {
  test("renders a companion check-in and today context inputs", () => {
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
    expect(html).toContain('placeholder="mood"');
    expect(html).toContain('placeholder="energy"');
    expect(html).toContain('placeholder="note"');
  });

  test("renders quick mood and energy controls", () => {
    const html = renderToStaticMarkup(
      <TodayContextPanel
        context={null}
        onSave={() => {}}
        onClear={() => {}}
      />,
    );

    expect(html).toContain('aria-label="Set mood tired"');
    expect(html).toContain('aria-label="Set mood anxious"');
    expect(html).toContain('aria-label="Set mood energized"');
    expect(html).toContain('aria-label="Set energy low"');
    expect(html).toContain('aria-label="Set energy steady"');
    expect(html).toContain('aria-label="Set energy high"');
  });
});
