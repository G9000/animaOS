import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const heroSource = readFileSync(
  new URL("../src/components/Hero.astro", import.meta.url),
  "utf8",
);

describe("site hero background", () => {
  test("defers the large video background until the browser is idle", () => {
    expect(heroSource).toContain("<AnimaLogoBg client:idle");
    expect(heroSource).not.toContain("<AnimaLogoBg client:load");
  });
});
