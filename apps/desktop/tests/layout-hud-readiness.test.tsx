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
) =>
  ({
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
  test("hides workspace navigation until the catalog capability is ready", () => {
    expect(
      renderLayoutHUD(statusWithCapabilities([])),
    ).toBe("");
  });

  test("renders workspace navigation when the catalog capability is ready", () => {
    expect(
      renderLayoutHUD(statusWithCapabilities(["navigation"])),
    ).not.toBe("");
  });
});
