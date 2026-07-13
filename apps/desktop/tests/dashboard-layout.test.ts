import { describe, expect, test } from "bun:test";

import {
  buildInitialNodes,
  type DashboardCallbacks,
  type DashboardInput,
} from "../src/pages/dashboard/layout";

const noop = () => {};

const callbacks: DashboardCallbacks = {
  onNavigate: noop,
  onExplore: noop,
  onToggleTask: noop,
  onDeleteTask: noop,
  onAddTask: noop,
  onEpisodeChat: noop,
  onEpisodeRead: noop,
  onViewAllEntries: noop,
  onDismissNudge: noop,
  onExploreMemory: noop,
  onCloseNode: noop,
  onImageClick: noop,
  onPreviewThread: noop,
  onOpenThread: noop,
  onNewChat: noop,
  onSaveCapture: async () => {},
  onNewEntry: noop,
};

const input: DashboardInput = {
  agentName: "Anima",
  avatarUrl: "",
  brief: null,
  briefLoading: false,
  reflection: null,
  reflectionLoading: false,
  tasks: [],
  episodes: [],
  mood: null,
  nudges: [],
  galleryImages: [],
  threads: [],
  journalEntries: [{ id: 1 }] as DashboardInput["journalEntries"],
};

describe("dashboard masonry layout", () => {
  test("positions the journal below the rendered recent chats node", () => {
    const nodes = buildInitialNodes(input, callbacks);
    const recentChats = nodes.find((node) => node.id === "recentChats");
    const journal = nodes.find((node) => node.id === "journal");

    expect(recentChats).toBeDefined();
    expect(journal).toBeDefined();
    expect(journal!.position.y).toBeGreaterThanOrEqual(
      recentChats!.position.y + (recentChats!.height ?? 0) + 24,
    );
  });
});
