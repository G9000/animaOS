import type { GraphEntity, GraphEntityDetail, GraphOverviewData } from "@anima/api-client";

export interface SearchResults {
  entities: Array<{
    id: number;
    name: string;
    type: string;
    mentions: number;
  }>;
  paths: Array<{
    source: string;
    relation: string;
    destination: string;
    source_type: string;
    destination_type: string;
  }>;
}

export interface GraphState {
  tab: "overview" | "entities" | "paths";
  overview: GraphOverviewData | null;
  entities: GraphEntity[];
  selectedEntity: GraphEntityDetail | null;
  searchQuery: string;
  searchResults: SearchResults | null;
  entityFilter: string;
  loading: boolean;
  entityOffset: number;
  entityTotal: number;
}
