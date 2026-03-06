import { useState, useEffect } from "react";
import { useAuth } from "../context/AuthContext";
import { api } from "../lib/api";

interface MemoryEntry {
  path: string;
  meta: {
    category: string;
    tags: string[];
    created: string;
    updated: string;
    source: string;
  };
  snippet: string;
}

interface MemoryFile {
  path: string;
  meta: {
    category: string;
    tags: string[];
    created: string;
    updated: string;
    source: string;
  };
  content: string;
}

export default function Memory() {
  const { user } = useAuth();
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [selectedFile, setSelectedFile] = useState<MemoryFile | null>(null);
  const [section, setSection] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editContent, setEditContent] = useState("");
  const [consolidating, setConsolidating] = useState(false);
  const [consolidateMsg, setConsolidateMsg] = useState("");

  const sections = ["all", "user", "knowledge", "relationships", "journal"];

  useEffect(() => {
    if (!user?.id) return;
    loadMemories();
  }, [user?.id, section]);

  const loadMemories = async () => {
    if (!user?.id) return;
    setLoading(true);
    try {
      const url =
        section === "all"
          ? `http://localhost:3031/api/memory/${user.id}`
          : `http://localhost:3031/api/memory/${user.id}?section=${section}`;
      const res = await fetch(url);
      const data = await res.json();
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
      const res = await fetch(
        `http://localhost:3031/api/memory/${user.id}/search?q=${encodeURIComponent(searchQuery)}`,
      );
      const data = await res.json();
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
      const res = await fetch(
        `http://localhost:3031/api/memory/${user.id}/${sectionName}/${filename}`,
      );
      const data = await res.json();
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
      await fetch(
        `http://localhost:3031/api/memory/${user.id}/${sectionName}/${filename}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            content: editContent,
            tags: selectedFile.meta.tags,
          }),
        },
      );
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
      await fetch(
        `http://localhost:3031/api/memory/${user.id}/${sectionName}/${filename}`,
        { method: "DELETE" },
      );
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

  const displayMemories = searchQuery ? searchResults : memories;
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
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              placeholder="Search..."
              className="flex-1 bg-(--color-bg-input) border border-(--color-border) rounded-sm px-2 py-1 text-[11px] text-(--color-text) placeholder:text-(--color-text-muted)/50 outline-none focus:border-(--color-primary)"
            />
            <button
              onClick={handleSearch}
              className="text-[10px] text-(--color-text-muted) hover:text-(--color-text) px-1"
            >
              Go
            </button>
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
        </div>

        {/* File List */}
        <div className="flex-1 overflow-y-auto p-3">
          {loading && !selectedFile && (
            <div className="text-(--color-text-muted) text-xs">Loading...</div>
          )}

          {!loading && displayMemories.length === 0 && (
            <div className="text-(--color-text-muted) text-xs">
              {searchQuery ? "No results" : "No memories yet"}
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
