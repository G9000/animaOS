import type { User } from "@anima/api-client";

export function authUserIdentity(response: User): User {
  const { id, username, name, gender, age, birthday, createdAt, updatedAt, ownerId, profileAvailable, filesystemAvailable } = response;
  return { id, username, name, gender, age, birthday, createdAt, updatedAt, ownerId, profileAvailable, filesystemAvailable };
}

export function accountDisplayName(user: User | null): string {
  if (user?.profileAvailable === false) return "Core owner";
  return user?.name || "User";
}
