import { describe, expect, test } from "bun:test";

import { createApiClient } from "@anima/api-client";
import {
  beginRecoveryPhraseReview,
  completeRecoveryPhraseReview,
  validateNewPassword,
  validateRecoveryPhraseConfirmation,
} from "../src/pages/settings/recoveryCredential";

describe("recovery credential replacement", () => {
  test("prepares then confirms with the typed-back phrase and no bearer token", async () => {
    const requests: Array<{ url: string; init?: RequestInit }> = [];
    const client = createApiClient({
      baseUrl: "http://anima.test/api",
      getUnlockToken: () => "unlock-token",
      fetchImpl: async (input, init) => {
        requests.push({ url: String(input), init });
        return new Response(
          JSON.stringify({
            success: true,
            recoveryPhrase: "new phrase words",
            pendingGeneration: 2,
            scope: "full",
          }),
          { status: 200, headers: { "content-type": "application/json" } },
        );
      },
    });

    const result = await client.auth.prepareRecoveryCredential(
      "current phrase words",
      "current-password",
      "full",
    );
    await client.auth.confirmRecoveryCredential(
      result.recoveryPhrase,
      result.pendingGeneration,
      result.scope,
      "current-password",
    );

    expect(result.recoveryPhrase).toBe("new phrase words");
    expect(requests).toHaveLength(2);
    expect(requests[0]?.url).toBe("http://anima.test/api/auth/recovery-credential/prepare");
    expect(JSON.parse(String(requests[0]?.init?.body))).toEqual({
      currentRecoveryPhrase: "current phrase words",
      currentPassword: "current-password",
      scope: "full",
    });
    expect(requests[1]?.url).toBe("http://anima.test/api/auth/recovery-credential/confirm");
    expect(JSON.parse(String(requests[1]?.init?.body))).toEqual({
      recoveryPhrase: "new phrase words",
      pendingGeneration: 2,
      scope: "full",
      currentPassword: "current-password",
    });
    expect(new Headers(requests[0]?.init?.headers).get("x-anima-unlock")).toBe(
      "unlock-token",
    );
  });

  test("keeps the phrase only until the user types it back exactly", () => {
    const review = beginRecoveryPhraseReview(
      "alpha beta gamma",
      2,
      "full",
      "current-password",
    );
    expect(review.phrase).toBe("alpha beta gamma");
    expect(review.currentPassword).toBe("current-password");

    const mismatch = validateRecoveryPhraseConfirmation(review, "alpha beta");
    expect(mismatch.phase).toBe("review");
    expect(mismatch.phrase).toBe("alpha beta gamma");
    expect(mismatch.currentPassword).toBe("current-password");
    expect(mismatch.error).toBe("Type the new recovery phrase exactly to confirm it.");

    const typedBack = validateRecoveryPhraseConfirmation(mismatch, "alpha beta gamma");
    expect(typedBack.phase).toBe("review");
    expect(typedBack.phrase).toBe("alpha beta gamma");

    const confirmed = completeRecoveryPhraseReview(typedBack);
    expect(confirmed.phase).toBe("complete");
    expect(confirmed.phrase).toBeNull();
    expect(confirmed.currentPassword).toBeNull();
    expect(confirmed.error).toBeNull();
  });

  test("uses the API minimum of eight characters for password changes", () => {
    expect(validateNewPassword("1234567")).toBe(
      "New password must be at least 8 characters.",
    );
    expect(validateNewPassword("12345678")).toBeNull();
  });
});
