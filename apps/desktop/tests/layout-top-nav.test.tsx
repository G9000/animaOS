import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import { TOP_NAV_ITEMS } from "../src/components/layout/nav-items";
import { TopNav } from "../src/features/hud/TopNav";

describe("TopNav", () => {
  test("renders the current compact navigation contract", () => {
    const html = renderToStaticMarkup(
      <MemoryRouter initialEntries={["/journal"]}>
        <TopNav />
      </MemoryRouter>,
    );

    for (const item of TOP_NAV_ITEMS) {
      expect(html).toContain(`href="${item.to}"`);
    }
    expect(html.match(/<a /g)).toHaveLength(TOP_NAV_ITEMS.length);
    expect(html).toContain('href="/journal"');
    expect(html).toContain("bg-accent text-accent-foreground");
  });
});
