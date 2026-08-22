import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  greetingForLocale,
  isRtlLocale,
  resolveSystemLocale,
} from "../src/lib/preUnlockEnvironment";

function source(path: string): string {
  return readFileSync(join(import.meta.dir, "..", path), "utf8");
}

describe("neutral pre-unlock surface", () => {
  test("uses the operating-system locale without portable profile data", () => {
    expect(resolveSystemLocale(["ms-MY"], "en-US")).toBe("ms-MY");
    expect(resolveSystemLocale(["not_a_locale"], "en-US")).toBe("en-US");
    expect(greetingForLocale("ms-MY")).toBe("selamat datang");
    expect(greetingForLocale("ar-SA")).toBe("مرحبا");
    expect(isRtlLocale("ar-SA")).toBe(true);
    expect(isRtlLocale("en-US")).toBe(false);
  });

  test("does not cache or render a private identity before unlock", () => {
    const login = source("src/pages/auth/Login.tsx");
    const auth = source("src/context/AuthContext.tsx");
    const shell = source("src/components/AtmosphereShell.tsx");

    expect(login).not.toContain("localStorage");
    expect(login).not.toContain("welcome, ${username}");
    expect(login).not.toMatch(/avatar|agentName|personaAvatar|useAgentProfile/);
    expect(auth).toContain('"anima_last_user"');
    expect(auth).toContain('localStorage.removeItem("anima_last_user")');
    expect(shell).toContain("isAuthenticated && <AtmosphereControls />");
  });

  test("binds locked rendering to OS direction and accessibility media", () => {
    const environment = source("src/lib/preUnlockEnvironment.ts");
    const main = source("src/main.tsx");
    const css = source("src/index.css");
    const ascii = source("../../packages/ascii-motion/src/AsciiBackground.tsx");

    expect(main).toContain("initializePreUnlockEnvironment();");
    expect(environment).toContain('root.dir = isRtlLocale(locale) ? "rtl" : "ltr"');
    expect(environment).toContain("prefers-reduced-motion: reduce");
    expect(environment).toContain("prefers-contrast: more");
    expect(environment).toContain("forced-colors: active");
    expect(css).toContain("animation-duration: 0.01ms !important");
    expect(ascii).toContain('reducedMotion.addEventListener("change", applyMotionPreference)');
    expect(ascii).toContain("paintFrame(canvas, data, 0, optsRef.current)");
  });
});
