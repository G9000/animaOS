import { describe, expect, test } from "bun:test";
import type { CoreFSSecurityStatus } from "@anima/api-client";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import { AuthProvider } from "../src/context/AuthContext";
import { CoreFSReadinessProvider } from "../src/context/CoreFSReadinessContext";
import { LayoutActionsProvider } from "../src/context/LayoutActionsContext";
import { LayoutHUD } from "../src/features/hud/LayoutHUD";

const statusWithCapabilities = (
  capabilities: CoreFSSecurityStatus["readiness"]["capabilities"],
  filesystemAvailable = true,
) =>
  ({
    filesystemAvailable,
    readiness: { capabilities },
  }) as CoreFSSecurityStatus;

function renderLayoutHUD(initialStatus: CoreFSSecurityStatus | null) {
  return renderToStaticMarkup(
    <MemoryRouter>
      <AuthProvider>
        <CoreFSReadinessProvider initialStatus={initialStatus}>
          <LayoutActionsProvider>
            <LayoutHUD />
          </LayoutActionsProvider>
        </CoreFSReadinessProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

describe("LayoutHUD readiness", () => {
  test("keeps current navigation available to Soul-only sessions", () => {
    const html = renderLayoutHUD(statusWithCapabilities([], false));

    expect(html).toContain('href="/settings"');
    expect(html).toContain('href="/consciousness"');
    expect(html).toContain('href="/journal"');
    expect(html).toContain('href="/memory"');
    expect(html).toContain('href="/knowledge"');
  });

  test("gates a full session until its catalog is authenticated", () => {
    expect(renderLayoutHUD(statusWithCapabilities([], true))).toBe("");
  });

  test("renders workspace navigation when the catalog capability is ready", () => {
    expect(
      renderLayoutHUD(statusWithCapabilities(["navigation"])),
    ).not.toBe("");
  });
});
