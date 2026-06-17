import { describe, expect, test } from "bun:test";

import { SIDEBAR_NAV_ITEMS, TOP_NAV_ITEMS } from "../src/components/layout/nav-items";

describe("desktop navigation", () => {
  test("exposes Diary as a first-class route in the sidebar", () => {
    const diaryIndex = SIDEBAR_NAV_ITEMS.findIndex((item) => item.to === "/journal");

    expect(diaryIndex).toBeGreaterThan(-1);
    expect(SIDEBAR_NAV_ITEMS[diaryIndex]).toMatchObject({
      label: "Diary",
      description: "logs",
    });
    expect(SIDEBAR_NAV_ITEMS[diaryIndex - 1]?.label).toBe("Chat");
    expect(SIDEBAR_NAV_ITEMS[diaryIndex + 1]?.label).toBe("Memory");
  });

  test("keeps Diary visible in the compact top navigation", () => {
    const diaryIndex = TOP_NAV_ITEMS.findIndex((item) => item.to === "/journal");

    expect(diaryIndex).toBeGreaterThan(-1);
    expect(TOP_NAV_ITEMS[diaryIndex]?.label).toBe("Diary");
    expect(TOP_NAV_ITEMS[diaryIndex - 1]?.label).toBe("Chat");
    expect(TOP_NAV_ITEMS[diaryIndex + 1]?.label).toBe("Memory");
  });
});
