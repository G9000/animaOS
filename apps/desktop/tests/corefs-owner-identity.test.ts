import { expect, test } from "bun:test";
import { accountDisplayName, authUserIdentity } from "../src/lib/userIdentity";

test("Soul-only identity stays explicit without retaining recovery secrets", () => {
  const response = { id: 0, username: "", name: "", ownerId: "owner-id", profileAvailable: false, filesystemAvailable: false, unlockToken: "token", recoveryPhrase: "secret" };
  const identity = authUserIdentity(response);
  expect(identity.id).toBe(0);
  expect(identity.ownerId).toBe("owner-id");
  expect(identity.profileAvailable).toBe(false);
  expect(identity.filesystemAvailable).toBe(false);
  expect(accountDisplayName(identity)).toBe("Core owner");
  expect("unlockToken" in identity).toBe(false);
  expect("recoveryPhrase" in identity).toBe(false);
});

test("unlocked current account displays its authenticated profile name", () => {
  expect(accountDisplayName({ id: 1, username: "owner", name: "Private Name", profileAvailable: true })).toBe("Private Name");
});
