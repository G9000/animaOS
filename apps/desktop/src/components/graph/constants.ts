export type Tab = "overview" | "entities" | "paths";

export const ENTITY_TYPES = [
  { key: "all", label: "ALL" },
  { key: "person", label: "PERSON" },
  { key: "place", label: "PLACE" },
  { key: "organization", label: "ORG" },
  { key: "project", label: "PROJECT" },
  { key: "concept", label: "CONCEPT" },
  { key: "unknown", label: "UNKNOWN" },
];

export const RELATION_COLORS: Record<string, string> = {
  lives_in: "text-emerald-400",
  works_at: "text-blue-400",
  sister_of: "text-pink-400",
  brother_of: "text-pink-400",
  parent_of: "text-purple-400",
  married_to: "text-rose-400",
  friend_of: "text-amber-400",
  colleague_of: "text-cyan-400",
  related_to_project: "text-indigo-400",
  interested_in: "text-teal-400",
  member_of: "text-violet-400",
  located_in: "text-lime-400",
  part_of: "text-orange-400",
  created_by: "text-sky-400",
};

export const ENTITY_LIMIT = 50;
