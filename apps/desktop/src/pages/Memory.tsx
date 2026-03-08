import { useState, useEffect } from "react";
import { useAuth } from "../context/AuthContext";
import { api, type MemoryEntry, type MemoryFile } from "../lib/api";

export default function Memory() {
  const { user } = useAuth();
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [selectedFile, setSelectedFile] = useState<MemoryFile | null>(null);
  const [section, setSection] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchPerformed, setSearchPerformed] = useState(false);
  const [searchResults, setSearchResults] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [newSection, setNewSection] = useState("user");
  const [sectionMode, setSectionMode] = useState<"preset" | "custom">("preset");
  const [selectedPresetSection, setSelectedPresetSection] = useState("user");
  const [newFilename, setNewFilename] = useState("");
  const [newContent, setNewContent] = useState("");
  const [newTags, setNewTags] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [consolidating, setConsolidating] = useState(false);
  const [consolidateMsg, setConsolidateMsg] = useState("");

  const defaultSections = ["user", "knowledge", "relationships", "journal"];

  useEffect(() => {
    if (!user?.id) return;
    loadMemories();
  }, [user?.id, section]);

  const loadMemories = async () => {
    if (!user?.id) return;
    setLoading(true);
    try {
      const data = await api.memory.list(
        user.id,
        section === "all" ? undefined : section,
      );
      setMemories(data.memories || []);
    } catch (err) {
      console.error("Failed to load memories:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleSearch = async () => {
    if (!user?.id || !searchQuery.trim()) return;
    setLoading(true);
    try {
      setSearchPerformed(true);
      const data = await api.memory.search(user.id, searchQuery);
      setSearchResults(data.results || []);
    } catch (err) {
      console.error("Search failed:", err);
    } finally {
      setLoading(false);
    }
  };

  const openFile = async (path: string) => {
    if (!user?.id) return;
    setLoading(true);
    setIsEditing(false);
    try {
      const parts = path.split("/");
      const sectionName = parts[0];
      const filename = parts.slice(2).join("/");
      const data = await api.memory.read(user.id, sectionName, filename);
      setSelectedFile(data);
      setEditContent(data.content);
    } catch (err) {
      console.error("Failed to open file:", err);
    } finally {
      setLoading(false);
    }
  };

  const startEdit = () => {
    if (selectedFile) {
      setEditContent(selectedFile.content);
      setIsEditing(true);
    }
  };

  const cancelEdit = () => {
    setIsEditing(false);
    if (selectedFile) {
      setEditContent(selectedFile.content);
    }
  };

  const saveFile = async () => {
    if (!user?.id || !selectedFile) return;
    setLoading(true);
    try {
      const parts = selectedFile.path.split("/");
      const sectionName = parts[0];
      const filename = parts.slice(2).join("/");
      await api.memory.write(user.id, sectionName, filename, {
        content: editContent,
        tags: selectedFile.meta.tags,
      });
      await openFile(selectedFile.path);
      setIsEditing(false);
      loadMemories();
    } catch (err) {
      console.error("Failed to save file:", err);
    } finally {
      setLoading(false);
    }
  };

  const deleteFile = async (path: string) => {
    if (!user?.id || !confirm("Delete this memory file?")) return;
    try {
      const parts = path.split("/");
      const sectionName = parts[0];
      const filename = parts.slice(2).join("/");
      await api.memory.remove(user.id, sectionName, filename);
      setSelectedFile(null);
      loadMemories();
    } catch (err) {
      console.error("Failed to delete file:", err);
    }
  };

  const handleConsolidate = async () => {
    if (!user?.id || consolidating) return;
    setConsolidating(true);
    setConsolidateMsg("");
    try {
      const result = await api.chat.consolidate(user.id);
      setConsolidateMsg(
        `${result.filesChanged}/${result.filesProcessed} files consolidated`,
      );
      setTimeout(() => setConsolidateMsg(""), 4000);
      loadMemories();
    } catch (err: any) {
      setConsolidateMsg(`Error: ${err.message}`);
    } finally {
      setConsolidating(false);
    }
  };

  const createFile = async () => {
    if (!user?.id) return;
    if (!newFilename.trim()) return;
    const sectionSource =
      sectionMode === "preset" ? selectedPresetSection : newSection;
    const sectionName =
      sectionSource
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9_-]/g, "-")
        .replace(/-{2,}/g, "-")
        .replace(/^-+|-+$/g, "") || "knowledge";

    setLoading(true);
    try {
      const tags = newTags
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean);

      await api.memory.write(user.id, sectionName, newFilename.trim(), {
        content: newContent || "# New Memory\n",
        tags,
      });

      const createdPath = `${sectionName}/${user.id}/${newFilename.trim().endsWith(".md") ? newFilename.trim() : `${newFilename.trim()}.md`}`;
      setSection("all");
      setSearchQuery("");
      setSearchResults([]);
      await loadMemories();
      await openFile(createdPath);

      setNewFilename("");
      setNewContent("");
      setNewTags("");
      setNewSection(sectionName);
      setSectionMode("preset");
      setSelectedPresetSection(defaultSections.includes(sectionName) ? sectionName : "knowledge");
      setShowCreate(false);
    } catch (err) {
      console.error("Failed to create file:", err);
    } finally {
      setLoading(false);
    }
  };

  const resetComposer = () => {
    setSectionMode("preset");
    setSelectedPresetSection("user");
    setNewSection("user");
    setNewFilename("");
    setNewTags("");
    setNewContent("");
    setShowCreate(false);
  };

  const groupBySection = (entries: MemoryEntry[]) => {
    return entries.reduce(
      (acc, entry) => {
        const s = entry.path.split("/")[0];
        if (!acc[s]) acc[s] = [];
        acc[s].push(entry);
        return acc;
      },
      {} as Record<string, MemoryEntry[]>,
    );
  };

  const discoveredSections = Array.from(
    new Set(memories.map((entry) => entry.path.split("/")[0]).filter(Boolean)),
  ).sort();
  const sections = ["all", ...Array.from(new Set([...defaultSections, ...discoveredSections]))];
  const displayMemories =
    searchPerformed && searchQuery.trim() ? searchResults : memories;
  const grouped = groupBySection(displayMemories);

  return (
    <div className="flex h-full">
      {/* Sidebar - File Tree */}
      <div className="w-72 border-r border-(--color-border) flex flex-col shrink-0">
        {/* Controls */}
        <div className="px-3 py-3 border-b border-(--color-border) space-y-2.5">
          {/* Section Filter */}
          <div className="flex gap-1 flex-wrap">
            {sections.map((s) => (
              <button
                key={s}
                onClick={() => setSection(s)}
                className={`text-[10px] px-1.5 py-0.5 rounded uppercase tracking-wider transition-colors ${
                  section === s
                    ? "bg-(--color-primary) text-(--color-bg)"
                    : "bg-(--color-bg-card) text-(--color-text-muted) hover:text-(--color-text)"
                }`}
              >
                {s}
              </button>
            ))}
          </div>

          {/* Search */}
          <div className="flex gap-1.5">
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => {
                const nextQuery = e.target.value;
                setSearchQuery(nextQuery);
                if (!nextQuery.trim()) {
                  setSearchPerformed(false);
                  setSearchResults([]);
                }
              }}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              placeholder="Search..."
              className="flex-1 bg-(--color-bg-input) border border-(--color-border) rounded-sm px-2 py-1 text-[11px] text-(--color-text) placeholder:text-(--color-text-muted)/50 outline-none focus:border-(--color-primary)"
            />
            <button
              onClick={handleSearch}
              className="text-[10px] text-(--color-text-muted) hover:text-(--color-text) px-1 uppercase tracking-wider"
            >
              Go
            </button>
            {searchPerformed && searchQuery.trim() && (
              <button
                onClick={() => {
                  setSearchQuery("");
                  setSearchPerformed(false);
                  setSearchResults([]);
                }}
                className="text-[10px] text-(--color-text-muted) hover:text-(--color-text) px-1 uppercase tracking-wider"
              >
                Clear
              </button>
            )}
          </div>

          {/* Consolidate */}
          <div className="flex items-center gap-2">
            <button
              onClick={handleConsolidate}
              disabled={consolidating}
              className="text-[10px] px-1.5 py-0.5 rounded-sm bg-(--color-bg-card) text-(--color-text-muted) hover:text-(--color-text) border border-(--color-border) hover:border-(--color-text-muted) uppercase tracking-wider transition-colors disabled:opacity-50"
            >
              {consolidating ? "Working..." : "Consolidate"}
            </button>
            {consolidateMsg && (
              <span className="text-[10px] text-(--color-text-muted)">
                {consolidateMsg}
              </span>
            )}
          </div>

          {/* New file */}
          <div className="pt-1 border-t border-(--color-border)">
            <button
              onClick={() => setShowCreate((prev) => !prev)}
              className="w-full text-left text-[10px] text-(--color-text-muted) hover:text-(--color-text) uppercase tracking-wider"
            >
              {showCreate ? "Hide" : "New File"}
            </button>
            {showCreate && (
              <div className="mt-1.5 space-y-2 border border-(--color-border) rounded-sm p-2">
                <div className="text-[10px] text-(--color-text-muted) uppercase tracking-wider">
                  Create Memory File
                </div>

                <div>
                  <label className="block text-[10px] text-(--color-text-muted) mb-1 uppercase tracking-wider">
                    Section
                  </label>
                  <select
                    value={sectionMode === "preset" ? selectedPresetSection : "__custom__"}
                    onChange={(e) => {
                      const value = e.target.value;
                      if (value === "__custom__") {
                        setSectionMode("custom");
                        setNewSection("");
                        return;
                      }
                      setSectionMode("preset");
                      setSelectedPresetSection(value);
                    }}
                    className="w-full bg-(--color-bg-input) border border-(--color-border) rounded-sm px-2 py-1 text-[11px] text-(--color-text) outline-none focus:border-(--color-primary)"
                  >
                    {defaultSections.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                    {sections
                      .filter((s) => s !== "all" && !defaultSections.includes(s))
                      .map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    <option value="__custom__">custom...</option>
                  </select>
                  {sectionMode === "custom" && (
                    <input
                      type="text"
                      value={newSection}
                      onChange={(e) => setNewSection(e.target.value)}
                      placeholder="custom section, e.g. habits"
                      className="mt-1.5 w-full bg-(--color-bg-input) border border-(--color-border) rounded-sm px-2 py-1 text-[11px] text-(--color-text) placeholder:text-(--color-text-muted)/50 outline-none focus:border-(--color-primary)"
                    />
                  )}
                </div>

                <div>
                  <label className="block text-[10px] text-(--color-text-muted) mb-1 uppercase tracking-wider">
                    Filename
                  </label>
                  <input
                    type="text"
                    value={newFilename}
                    onChange={(e) => setNewFilename(e.target.value)}
                    placeholder="e.g. morning-routine"
                    className="w-full bg-(--color-bg-input) border border-(--color-border) rounded-sm px-2 py-1 text-[11px] text-(--color-text) placeholder:text-(--color-text-muted)/50 outline-none focus:border-(--color-primary)"
                  />
                </div>

                <input
                  type="text"
                  value={newTags}
                  onChange={(e) => setNewTags(e.target.value)}
                  placeholder="tags (optional, comma separated)"
                  className="w-full bg-(--color-bg-input) border border-(--color-border) rounded-sm px-2 py-1 text-[11px] text-(--color-text) placeholder:text-(--color-text-muted)/50 outline-none focus:border-(--color-primary)"
                />
                <textarea
                  value={newContent}
                  onChange={(e) => setNewContent(e.target.value)}
                  placeholder="initial content (markdown)"
                  className="w-full h-24 bg-(--color-bg-input) border border-(--color-border) rounded-sm px-2 py-1 text-[11px] text-(--color-text) placeholder:text-(--color-text-muted)/50 outline-none focus:border-(--color-primary) resize-none"
                />
                <div className="text-[10px] text-(--color-text-muted)">
                  Path: {(
                    (sectionMode === "preset" ? selectedPresetSection : newSection).trim() ||
                    "knowledge"
                  ).toLowerCase()}/
                  {newFilename.trim() || "filename"}.md
                </div>
                <div className="flex gap-1.5">
                  <button
                    onClick={resetComposer}
                    className="flex-1 text-[10px] px-1.5 py-1 rounded-sm bg-(--color-bg-input) text-(--color-text-muted) hover:text-(--color-text) border border-(--color-border) uppercase tracking-wider transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={createFile}
                    disabled={loading || !newFilename.trim()}
                    className="flex-1 text-[10px] px-1.5 py-1 rounded-sm bg-(--color-bg-card) text-(--color-text-muted) hover:text-(--color-text) border border-(--color-border) hover:border-(--color-text-muted) uppercase tracking-wider transition-colors disabled:opacity-50"
                  >
                    Create
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* File List */}
        <div className="flex-1 overflow-y-auto p-3">
          {loading && !selectedFile && (
            <div className="text-(--color-text-muted) text-xs">Loading...</div>
          )}

          {!loading && displayMemories.length === 0 && (
            <div className="text-(--color-text-muted) text-xs">
              {searchPerformed && searchQuery.trim()
                ? "No results"
                : "No memories yet"}
            </div>
          )}

          {!loading &&
            Object.entries(grouped).map(([sectionName, entries]) => (
              <div key={sectionName} className="mb-3">
                <div className="text-[10px] text-(--color-text-muted) uppercase tracking-wider mb-1.5 flex items-center gap-1.5">
                  <span className="text-(--color-primary)">▸</span>
                  {sectionName}
                  <span className="text-(--color-text-muted)/40">({entries.length})</span>
                </div>
                <div className="space-y-0.5 pl-2.5">
                  {entries.map((entry) => (
                    <button
                      key={entry.path}
                      onClick={() => openFile(entry.path)}
                      className={`block w-full text-left text-[11px] px-2 py-1.5 rounded-sm transition-colors ${
                        selectedFile?.path === entry.path
                          ? "bg-(--color-bg-card) text-(--color-text)"
                          : "text-(--color-text-muted) hover:text-(--color-text) hover:bg-(--color-bg-card)/50"
                      }`}
                    >
                      <div className="truncate">
                        {entry.path.split("/").pop()?.replace(".md", "")}
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
        </div>
      </div>

      {/* Main Content - File Viewer */}
      <div className="flex-1 flex flex-col min-w-0">
        {selectedFile ? (
          <>
            {/* File Header */}
            <div className="px-5 py-3 border-b border-(--color-border)">
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <h1 className="text-xs tracking-wider uppercase truncate">
                    {selectedFile.path}
                  </h1>
                  <div className="flex gap-2 mt-1 text-[10px] text-(--color-text-muted)">
                    <span>{selectedFile.meta.category}</span>
                    <span className="text-(--color-border)">·</span>
                    <span>
                      {new Date(selectedFile.meta.updated).toLocaleDateString()}
                    </span>
                    <span className="text-(--color-border)">·</span>
                    <span>{selectedFile.meta.source}</span>
                  </div>
                  {selectedFile.meta.tags.length > 0 && (
                    <div className="flex gap-1.5 mt-1.5">
                      {selectedFile.meta.tags.map((tag) => (
                        <span
                          key={tag}
                          className="text-[9px] px-1.5 py-0.5 bg-(--color-bg-card) border border-(--color-border) rounded uppercase tracking-wider text-(--color-text-muted)"
                        >
                          {tag}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex gap-2 shrink-0">
                  {isEditing ? (
                    <>
                      <button
                        onClick={cancelEdit}
                        className="text-[10px] text-(--color-text-muted) hover:text-(--color-text) uppercase tracking-wider"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={saveFile}
                        disabled={loading}
                        className="text-[10px] text-(--color-text-muted) hover:text-(--color-text) uppercase tracking-wider disabled:opacity-50"
                      >
                        Save
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={startEdit}
                        className="text-[10px] text-(--color-text-muted) hover:text-(--color-text) uppercase tracking-wider"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => deleteFile(selectedFile.path)}
                        className="text-[10px] text-(--color-text-muted) hover:text-(--color-danger) uppercase tracking-wider"
                      >
                        Del
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>

            {/* File Content */}
            <div className="flex-1 overflow-y-auto px-5 py-4">
              {isEditing ? (
                <textarea
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  className="w-full h-full bg-(--color-bg-input) border border-(--color-border) rounded-sm px-3 py-2 text-sm text-(--color-text) leading-relaxed outline-none focus:border-(--color-primary) resize-none"
                  spellCheck={false}
                />
              ) : (
                <pre className="text-sm text-(--color-text) whitespace-pre-wrap leading-relaxed">
                  {selectedFile.content}
                </pre>
              )}
            </div>
          </>
        ) : (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <p className="text-(--color-text-muted)/40 text-lg mb-2">◇</p>
              <p className="text-(--color-text-muted) text-xs">
                Select a memory file
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
