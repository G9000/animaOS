import { useState, useEffect } from "react";
import { useAuth } from "../context/AuthContext";
import type { GraphEntityDetail, GraphOverviewData, GraphEntity } from "../lib/api";
import { api } from "../lib/api";
import {
  EntityDetail,
  OverviewTab,
  EntitiesTab,
  PathsTab,
  GraphHeader,
  GraphTabs,
  type Tab,
  type SearchResults,
} from "../components/graph";
import { ENTITY_LIMIT } from "../components/graph/constants";

export default function KnowledgeGraph() {
  const { user } = useAuth();
  const [tab, setTab] = useState<Tab>("overview");
  const [overview, setOverview] = useState<GraphOverviewData | null>(null);
  const [entities, setEntities] = useState<GraphEntity[]>([]);
  const [selectedEntity, setSelectedEntity] = useState<GraphEntityDetail | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResults | null>(null);
  const [entityFilter, setEntityFilter] = useState("all");
  const [loading, setLoading] = useState(false);
  const [entityOffset, setEntityOffset] = useState(0);
  const [entityTotal, setEntityTotal] = useState(0);

  useEffect(() => {
    if (user?.id == null) return;
    loadOverview();
  }, [user?.id]);

  useEffect(() => {
    if (user?.id == null) return;
    if (tab === "entities") {
      loadEntities();
    }
  }, [user?.id, tab, entityFilter, entityOffset]);

  const loadOverview = async () => {
    if (user?.id == null) return;
    try {
      const data = await api.graph.overview(user.id);
      setOverview(data);
    } catch (err) {
      console.error("Failed to load graph overview:", err);
    }
  };

  const loadEntities = async () => {
    if (user?.id == null) return;
    setLoading(true);
    try {
      const type = entityFilter === "all" ? undefined : entityFilter;
      const data = await api.graph.entities(user.id, {
        type,
        limit: ENTITY_LIMIT,
        offset: entityOffset,
      });
      setEntities(data.entities);
      setEntityTotal(data.total);
    } catch (err) {
      console.error("Failed to load entities:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleEntityClick = async (entityId: number) => {
    if (user?.id == null) return;
    try {
      const data = await api.graph.entity(user.id, entityId);
      setSelectedEntity(data);
    } catch (err) {
      console.error("Failed to load entity:", err);
    }
  };

  const handleSearch = async () => {
    if (user?.id == null || !searchQuery.trim()) return;
    setLoading(true);
    try {
      const data = await api.graph.search(user.id, searchQuery.trim());
      setSearchResults(data);
      setTab("paths");
    } catch (err) {
      console.error("Failed to search graph:", err);
    } finally {
      setLoading(false);
    }
  };

  const clearSearch = () => {
    setSearchQuery("");
    setSearchResults(null);
  };

  const handleFilterClick = (type: string) => {
    setEntityFilter(type);
    setEntityOffset(0);
    setTab("entities");
  };

  const handleTabChange = (newTab: Tab) => {
    setTab(newTab);
    setSelectedEntity(null);
  };

  return (
    <div className="flex flex-col h-full">
      <GraphHeader
        overview={overview}
        searchQuery={searchQuery}
        searchResults={searchResults}
        loading={loading}
        onSearchChange={setSearchQuery}
        onSearch={handleSearch}
        onClear={clearSearch}
      />

      <GraphTabs tab={tab} onTabChange={handleTabChange} />

      <div className="flex-1 overflow-y-auto px-5 py-4">
        {tab === "overview" && overview && (
          <OverviewTab
            overview={overview}
            onEntityClick={handleEntityClick}
            onFilterClick={handleFilterClick}
          />
        )}

        {tab === "entities" && (
          <EntitiesTab
            entities={entities}
            entityFilter={entityFilter}
            entityOffset={entityOffset}
            entityTotal={entityTotal}
            loading={loading}
            onFilterChange={(filter) => { setEntityFilter(filter); setEntityOffset(0); }}
            onEntityClick={handleEntityClick}
            onOffsetChange={setEntityOffset}
          />
        )}

        {tab === "paths" && (
          <PathsTab
            searchResults={searchResults}
            onEntityClick={handleEntityClick}
          />
        )}
      </div>

      {selectedEntity && (
        <EntityDetail
          entity={selectedEntity}
          onClose={() => setSelectedEntity(null)}
          onEntityClick={handleEntityClick}
        />
      )}
    </div>
  );
}
