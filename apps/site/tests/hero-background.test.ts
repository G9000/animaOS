import { describe, expect, test } from "bun:test";
import { readFileSync, statSync } from "node:fs";

import { demux } from "../../../packages/ascii-motion/src/demux";

const heroSource = readFileSync(
  new URL("../src/components/Hero.astro", import.meta.url),
  "utf8",
);
const heroVideo = new URL("../public/login-bg.mp4", import.meta.url);

describe("site hero background", () => {
  test("defers the large video background until the browser is idle", () => {
    expect(heroSource).toContain("<AnimaLogoBg client:idle");
    expect(heroSource).not.toContain("<AnimaLogoBg client:load");
  });

  test("keeps the decoded hero video within a lightweight asset budget", () => {
    expect(statSync(heroVideo).size).toBeLessThan(8 * 1024 * 1024);
  });

  test("keeps the optimized video compatible with the ASCII decoder", () => {
    const bytes = readFileSync(heroVideo);
    const buffer = bytes.buffer.slice(
      bytes.byteOffset,
      bytes.byteOffset + bytes.byteLength,
    ) as ArrayBuffer;
    const video = demux(buffer);

    expect(video.codec).toStartWith("avc1.");
    expect(video.duration).toBeGreaterThan(10);
    expect(video.samples.length).toBeLessThanOrEqual(200);
  });
});
