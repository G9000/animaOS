/**
 * Gmail helper tests.
 */

import { afterEach, describe, expect, it } from "bun:test";
import { readGmail, sendGmail } from "./gmail.js";

const originalFetch = globalThis.fetch;

function toBase64Url(value: string): string {
  return Buffer.from(value, "utf8")
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

function fromBase64Url(value: string): string {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized.padEnd(
    normalized.length + ((4 - (normalized.length % 4)) % 4),
    "=",
  );
  return Buffer.from(padded, "base64").toString("utf8");
}

describe("Gmail helpers", () => {
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("reads nested multipart plain-text bodies", async () => {
    globalThis.fetch = async () =>
      Response.json({
        id: "msg-1",
        threadId: "thread-1",
        snippet: "fallback snippet",
        internalDate: "1777507200000",
        payload: {
          headers: [
            { name: "From", value: "sender@example.com" },
            { name: "To", value: "user@example.com" },
            { name: "Subject", value: "Nested message" },
          ],
          parts: [
            {
              mimeType: "multipart/alternative",
              body: {},
              parts: [
                {
                  mimeType: "text/html",
                  body: { data: toBase64Url("<p>Nested HTML</p>") },
                },
                {
                  mimeType: "text/plain",
                  body: { data: toBase64Url("Nested plain text body") },
                },
              ],
            },
          ],
        },
      });

    const result = await readGmail("access-token", "msg-1");

    expect(result).toContain("Nested plain text body");
    expect(result).not.toContain("fallback snippet");
  });

  it("sends Unicode message bodies as UTF-8 base64url", async () => {
    let capturedRaw = "";
    globalThis.fetch = async (_input, init) => {
      capturedRaw = JSON.parse(String(init?.body)).raw;
      return Response.json({ id: "sent-1" });
    };

    const result = await sendGmail(
      "access-token",
      "friend@example.com",
      "Hello",
      "Emoji and accents: café 🙂",
    );

    expect(result).toContain("sent-1");
    expect(fromBase64Url(capturedRaw)).toContain("Emoji and accents: café 🙂");
  });
});
