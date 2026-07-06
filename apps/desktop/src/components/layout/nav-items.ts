import type { ComponentType } from "react";
import {
  ChatIcon,
  ConfigIcon,
  DatabaseIcon,
  DocumentIcon,
  HomeIcon,
  MemoryIcon,
  MindIcon,
  ModsIcon,
  PresenceIcon,
  TasksIcon,
  type IconProps,
} from "@anima/standard-templates";

export interface NavItem {
  to: string;
  label: string;
  Icon: ComponentType<IconProps>;
  description: string;
}

export const SIDEBAR_NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Home", Icon: HomeIcon, description: "dashboard" },
  { to: "/tasks", label: "Tasks", Icon: TasksIcon, description: "queue" },
  { to: "/chat", label: "Chat", Icon: ChatIcon, description: "console" },
  { to: "/journal", label: "Diary", Icon: DocumentIcon, description: "logs" },
  { to: "/memory", label: "Memory", Icon: MemoryIcon, description: "archive" },
  { to: "/knowledge", label: "Knowledge", Icon: DocumentIcon, description: "library" },
  { to: "/presence", label: "Presence", Icon: PresenceIcon, description: "signals" },
  { to: "/consciousness", label: "Mind", Icon: MindIcon, description: "consciousness" },
  { to: "/mods", label: "Mods", Icon: ModsIcon, description: "extensions" },
  { to: "/settings", label: "Settings", Icon: ConfigIcon, description: "system" },
];

export const TOP_NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Home", Icon: HomeIcon, description: "dashboard" },
  // { to: "/chat", label: "Chat", Icon: ChatIcon, description: "console" },
  { to: "/journal", label: "Diary", Icon: DocumentIcon, description: "logs" },
  { to: "/memory", label: "Memory", Icon: MemoryIcon, description: "archive" },
  { to: "/knowledge", label: "Knowledge", Icon: DocumentIcon, description: "library" },
  { to: "/consciousness", label: "Mind", Icon: MindIcon, description: "consciousness" },
  // { to: "/tasks", label: "Tasks", Icon: TasksIcon, description: "queue" },
  // { to: "/presence", label: "Presence", Icon: PresenceIcon, description: "signals" },
  // { to: "/mods", label: "Mods", Icon: ModsIcon, description: "extensions" },
  { to: "/settings", label: "Settings", Icon: ConfigIcon, description: "system" },
];

export const DATABASE_NAV_ITEM: NavItem = {
  to: "/database",
  label: "Database",
  Icon: DatabaseIcon,
  description: "inspector",
};
