//! In-memory Knowledge Graph with multi-hop traversal.
//!
//! Adapted from memvid's Logic-Mesh. SQL stays the persistence layer (write-back),
//! but traversal and dedup happen in Rust for O(1) neighbor lookup.
//!
//! On startup: load `kg_entities + kg_relations` from PG → in-memory graph.
//! On mutation: update in-memory, write-back to PG.

use std::collections::{HashMap, HashSet, VecDeque};

use serde::{Deserialize, Serialize};

use crate::frame::FrameId;

/// Maximum nodes (DoS prevention).
pub const MAX_NODES: usize = 1_000_000;
/// Maximum edges (DoS prevention).
pub const MAX_EDGES: usize = 5_000_000;

/// Entity classification.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum EntityKind {
    Person = 0,
    Organization = 1,
    Project = 2,
    Location = 3,
    Event = 4,
    Product = 5,
    Email = 6,
    Date = 7,
    Url = 8,
    Other = 255,
}

impl EntityKind {
    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Person => "person",
            Self::Organization => "organization",
            Self::Project => "project",
            Self::Location => "location",
            Self::Event => "event",
            Self::Product => "product",
            Self::Email => "email",
            Self::Date => "date",
            Self::Url => "url",
            Self::Other => "other",
        }
    }

    #[must_use]
    pub fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "person" | "per" => Self::Person,
            "organization" | "org" | "company" => Self::Organization,
            "project" => Self::Project,
            "location" | "loc" | "gpe" => Self::Location,
            "event" => Self::Event,
            "product" => Self::Product,
            "email" => Self::Email,
            "date" | "time" => Self::Date,
            "url" | "link" => Self::Url,
            _ => Self::Other,
        }
    }
}

impl std::fmt::Display for EntityKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

/// A node representing an entity in the knowledge graph.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphNode {
    /// Deterministic ID: hash(canonical_name, kind).
    pub id: u64,
    /// Normalized lowercase name.
    pub canonical_name: String,
    /// Original casing for display.
    pub display_name: String,
    /// Entity type.
    pub kind: EntityKind,
    /// Confidence score (0.0-1.0).
    pub confidence: f32,
    /// Frame IDs where this entity was mentioned (provenance).
    pub frame_ids: Vec<FrameId>,
    /// Optional description.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub description: Option<String>,
    /// Number of times mentioned.
    pub mentions: u32,
}

/// A directed edge representing a relationship between entities.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphEdge {
    /// Source node ID.
    pub from_node: u64,
    /// Target node ID.
    pub to_node: u64,
    /// Relationship type (flexible string, e.g., "employer", "located_in").
    pub relation_type: String,
    /// Confidence score (0.0-1.0).
    pub confidence: f32,
    /// Source frame.
    pub frame_id: FrameId,
    /// Number of times this edge was observed.
    pub mentions: u32,
}

/// Result from multi-hop graph traversal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FollowResult {
    pub node_name: String,
    pub node_kind: EntityKind,
    pub confidence: f32,
    pub frame_ids: Vec<FrameId>,
    pub path_length: usize,
}

/// Statistics about the knowledge graph.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct GraphStats {
    pub node_count: usize,
    pub edge_count: usize,
    pub entity_kinds: HashMap<String, usize>,
    pub relation_types: HashMap<String, usize>,
}

/// In-memory Knowledge Graph with O(1) neighbor lookup.
#[derive(Debug, Clone, Default)]
pub struct KnowledgeGraph {
    /// All nodes by ID.
    nodes: HashMap<u64, GraphNode>,
    /// All edges (indexed by edge key for dedup).
    edges: Vec<GraphEdge>,
    /// Adjacency list: node_id → [(edge_index, is_outgoing)].
    adjacency: HashMap<u64, Vec<(usize, bool)>>,
    /// Name lookup: canonical_name → node_id.
    name_index: HashMap<String, u64>,
}

impl KnowledgeGraph {
    pub fn new() -> Self {
        Self::default()
    }

    /// Compute deterministic node ID from canonical name and kind.
    fn compute_node_id(canonical_name: &str, kind: EntityKind) -> u64 {
        use std::hash::{Hash, Hasher};
        let mut hasher = std::collections::hash_map::DefaultHasher::new();
        canonical_name.hash(&mut hasher);
        (kind as u8).hash(&mut hasher);
        hasher.finish()
    }

    /// Tokenize a name for fuzzy matching (matching animaOS's knowledge_graph.py).
    fn tokenize_name(name: &str) -> HashSet<String> {
        name.to_lowercase()
            .split(|c: char| !c.is_alphanumeric())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
            .collect()
    }

    /// Jaccard similarity between two token sets.
    fn jaccard(a: &HashSet<String>, b: &HashSet<String>) -> f32 {
        if a.is_empty() && b.is_empty() {
            return 1.0;
        }
        let intersection = a.intersection(b).count() as f32;
        let union = a.union(b).count() as f32;
        if union == 0.0 {
            return 0.0;
        }
        intersection / union
    }

    /// Find the best matching existing node by fuzzy name match.
    /// Threshold: 0.7 (matching animaOS's knowledge_graph.py).
    fn find_fuzzy_match(&self, name: &str, kind: EntityKind) -> Option<u64> {
        let tokens = Self::tokenize_name(name);
        let canonical = name.to_lowercase();
        let mut best_match: Option<(u64, f32)> = None;

        for node in self.nodes.values() {
            // Only match same kind
            if node.kind != kind {
                continue;
            }

            let node_tokens = Self::tokenize_name(&node.canonical_name);
            let mut sim = Self::jaccard(&tokens, &node_tokens);

            // Boost if one contains the other as substring
            if canonical.contains(&node.canonical_name) || node.canonical_name.contains(&canonical)
            {
                sim = sim.max(0.8);
            }

            if sim >= 0.7 {
                if let Some((_, best_sim)) = best_match {
                    if sim > best_sim {
                        best_match = Some((node.id, sim));
                    }
                } else {
                    best_match = Some((node.id, sim));
                }
            }
        }

        best_match.map(|(id, _)| id)
    }

    /// Upsert a node. Deduplicates by fuzzy name match within the same kind.
    ///
    /// Returns the node ID (existing or newly created).
    pub fn upsert_node(
        &mut self,
        name: &str,
        kind: EntityKind,
        confidence: f32,
        frame_id: FrameId,
    ) -> crate::Result<u64> {
        if self.nodes.len() >= MAX_NODES {
            return Err(crate::Error::Graph(format!(
                "maximum node count ({MAX_NODES}) exceeded"
            )));
        }

        let canonical = name.to_lowercase().trim().to_string();

        // Try fuzzy match first
        if let Some(existing_id) = self.find_fuzzy_match(&canonical, kind) {
            if let Some(node) = self.nodes.get_mut(&existing_id) {
                node.mentions += 1;
                if !node.frame_ids.contains(&frame_id) {
                    node.frame_ids.push(frame_id);
                }
                // Update confidence if higher
                if confidence > node.confidence {
                    node.confidence = confidence;
                }
                return Ok(existing_id);
            }
        }

        // Create new node
        let id = Self::compute_node_id(&canonical, kind);
        let node = GraphNode {
            id,
            canonical_name: canonical.clone(),
            display_name: name.to_string(),
            kind,
            confidence,
            frame_ids: vec![frame_id],
            description: None,
            mentions: 1,
        };

        self.nodes.insert(id, node);
        self.name_index.insert(canonical, id);
        Ok(id)
    }

    /// Upsert an edge. Deduplicates by (from_node, to_node, relation_type).
    pub fn upsert_edge(
        &mut self,
        from_node: u64,
        to_node: u64,
        relation_type: &str,
        confidence: f32,
        frame_id: FrameId,
    ) -> crate::Result<()> {
        if self.edges.len() >= MAX_EDGES {
            return Err(crate::Error::Graph(format!(
                "maximum edge count ({MAX_EDGES}) exceeded"
            )));
        }

        // Check if edge already exists
        let rel_lower = relation_type.to_lowercase();
        for edge in self.edges.iter_mut() {
            if edge.from_node == from_node
                && edge.to_node == to_node
                && edge.relation_type.to_lowercase() == rel_lower
            {
                edge.mentions += 1;
                if confidence > edge.confidence {
                    edge.confidence = confidence;
                }
                return Ok(());
            }
        }

        // Create new edge
        let edge_idx = self.edges.len();
        let edge = GraphEdge {
            from_node,
            to_node,
            relation_type: relation_type.to_string(),
            confidence,
            frame_id,
            mentions: 1,
        };

        self.edges.push(edge);

        // Update adjacency
        self.adjacency
            .entry(from_node)
            .or_default()
            .push((edge_idx, true));
        self.adjacency
            .entry(to_node)
            .or_default()
            .push((edge_idx, false));

        Ok(())
    }

    /// Multi-hop traversal from a starting entity.
    ///
    /// Finds all reachable entities within `max_hops` following edges of the
    /// given relation type. If `relation_filter` is None, follows all edges.
    pub fn follow(
        &self,
        start_name: &str,
        relation_filter: Option<&str>,
        max_hops: usize,
    ) -> Vec<FollowResult> {
        let canonical = start_name.to_lowercase();
        let start_id = match self.name_index.get(&canonical) {
            Some(&id) => id,
            None => {
                // Try fuzzy match
                let kind_guess = self
                    .nodes
                    .values()
                    .find(|n| n.canonical_name == canonical)
                    .map(|n| n.kind)
                    .unwrap_or(EntityKind::Other);
                match self.find_fuzzy_match(&canonical, kind_guess) {
                    Some(id) => id,
                    None => return vec![],
                }
            }
        };

        let rel_filter = relation_filter.map(|r| r.to_lowercase());

        let mut visited = HashSet::new();
        visited.insert(start_id);

        let mut queue: VecDeque<(u64, usize)> = VecDeque::new();
        queue.push_back((start_id, 0));

        let mut results = Vec::new();

        while let Some((current_id, depth)) = queue.pop_front() {
            if depth >= max_hops {
                continue;
            }

            if let Some(adj) = self.adjacency.get(&current_id) {
                for &(edge_idx, _is_outgoing) in adj {
                    let edge = &self.edges[edge_idx];

                    // Apply relation filter
                    if let Some(ref filter) = rel_filter {
                        if edge.relation_type.to_lowercase() != *filter {
                            continue;
                        }
                    }

                    // Get the neighbor (other end of the edge)
                    let neighbor_id = if edge.from_node == current_id {
                        edge.to_node
                    } else {
                        edge.from_node
                    };

                    if visited.contains(&neighbor_id) {
                        continue;
                    }
                    visited.insert(neighbor_id);

                    if let Some(node) = self.nodes.get(&neighbor_id) {
                        results.push(FollowResult {
                            node_name: node.display_name.clone(),
                            node_kind: node.kind,
                            confidence: node.confidence,
                            frame_ids: node.frame_ids.clone(),
                            path_length: depth + 1,
                        });

                        queue.push_back((neighbor_id, depth + 1));
                    }
                }
            }
        }

        results
    }

    /// Get all neighbors of a node (1-hop).
    pub fn neighbors(&self, node_id: u64) -> Vec<(&GraphNode, &GraphEdge)> {
        self.adjacency
            .get(&node_id)
            .map(|adj| {
                adj.iter()
                    .filter_map(|&(edge_idx, _)| {
                        let edge = &self.edges[edge_idx];
                        let neighbor_id = if edge.from_node == node_id {
                            edge.to_node
                        } else {
                            edge.from_node
                        };
                        self.nodes.get(&neighbor_id).map(|node| (node, edge))
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Get all entities of a given kind.
    pub fn entities_by_kind(&self, kind: EntityKind) -> Vec<&GraphNode> {
        self.nodes.values().filter(|n| n.kind == kind).collect()
    }

    /// Look up a node by name (case-insensitive).
    pub fn get_by_name(&self, name: &str) -> Option<&GraphNode> {
        let canonical = name.to_lowercase();
        self.name_index
            .get(&canonical)
            .and_then(|&id| self.nodes.get(&id))
    }

    /// Get a node by ID.
    pub fn get_node(&self, id: u64) -> Option<&GraphNode> {
        self.nodes.get(&id)
    }

    /// Graph statistics.
    #[must_use]
    pub fn stats(&self) -> GraphStats {
        let mut entity_kinds = HashMap::new();
        for node in self.nodes.values() {
            *entity_kinds
                .entry(node.kind.as_str().to_string())
                .or_insert(0) += 1;
        }

        let mut relation_types = HashMap::new();
        for edge in &self.edges {
            *relation_types
                .entry(edge.relation_type.clone())
                .or_insert(0) += 1;
        }

        GraphStats {
            node_count: self.nodes.len(),
            edge_count: self.edges.len(),
            entity_kinds,
            relation_types,
        }
    }

    #[must_use]
    pub fn node_count(&self) -> usize {
        self.nodes.len()
    }

    #[must_use]
    pub fn edge_count(&self) -> usize {
        self.edges.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    /// Serialize the graph to JSON bytes.
    pub fn serialize(&self) -> crate::Result<Vec<u8>> {
        let data = SerializedGraph {
            nodes: self.nodes.values().cloned().collect(),
            edges: self.edges.clone(),
        };
        serde_json::to_vec(&data).map_err(|e| crate::Error::Serialization(e.to_string()))
    }

    /// Deserialize from JSON bytes and rebuild adjacency/name indices.
    pub fn deserialize(bytes: &[u8]) -> crate::Result<Self> {
        let data: SerializedGraph = serde_json::from_slice(bytes)
            .map_err(|e| crate::Error::Serialization(e.to_string()))?;

        let mut graph = Self::new();

        for node in data.nodes {
            graph
                .name_index
                .insert(node.canonical_name.clone(), node.id);
            graph.nodes.insert(node.id, node);
        }

        for (idx, edge) in data.edges.into_iter().enumerate() {
            graph
                .adjacency
                .entry(edge.from_node)
                .or_default()
                .push((idx, true));
            graph
                .adjacency
                .entry(edge.to_node)
                .or_default()
                .push((idx, false));
            graph.edges.push(edge);
        }

        Ok(graph)
    }
}

#[derive(Serialize, Deserialize)]
struct SerializedGraph {
    nodes: Vec<GraphNode>,
    edges: Vec<GraphEdge>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_upsert_node_new() {
        let mut graph = KnowledgeGraph::new();
        let id = graph
            .upsert_node("John Smith", EntityKind::Person, 0.9, 1)
            .unwrap();
        assert_eq!(graph.node_count(), 1);
        let node = graph.get_node(id).unwrap();
        assert_eq!(node.display_name, "John Smith");
        assert_eq!(node.canonical_name, "john smith");
    }

    #[test]
    fn test_upsert_node_dedup() {
        let mut graph = KnowledgeGraph::new();
        let id1 = graph
            .upsert_node("John Smith", EntityKind::Person, 0.9, 1)
            .unwrap();
        let id2 = graph
            .upsert_node("john smith", EntityKind::Person, 0.8, 2)
            .unwrap();

        assert_eq!(id1, id2, "same canonical name should dedup");
        assert_eq!(graph.node_count(), 1);

        let node = graph.get_node(id1).unwrap();
        assert_eq!(node.mentions, 2);
        assert_eq!(node.frame_ids, vec![1, 2]);
    }

    #[test]
    fn test_upsert_edge_dedup() {
        let mut graph = KnowledgeGraph::new();
        let id1 = graph
            .upsert_node("John", EntityKind::Person, 0.9, 1)
            .unwrap();
        let id2 = graph
            .upsert_node("Acme Corp", EntityKind::Organization, 0.9, 1)
            .unwrap();

        graph.upsert_edge(id1, id2, "employer", 0.8, 1).unwrap();
        graph.upsert_edge(id1, id2, "employer", 0.9, 2).unwrap();

        assert_eq!(graph.edge_count(), 1, "duplicate edge should dedup");
        assert_eq!(graph.edges[0].mentions, 2);
        assert_eq!(
            graph.edges[0].confidence, 0.9,
            "should update to higher confidence"
        );
    }

    #[test]
    fn test_follow_single_hop() {
        let mut graph = KnowledgeGraph::new();
        let john = graph
            .upsert_node("John", EntityKind::Person, 0.9, 1)
            .unwrap();
        let acme = graph
            .upsert_node("Acme Corp", EntityKind::Organization, 0.9, 1)
            .unwrap();
        let sf = graph
            .upsert_node("San Francisco", EntityKind::Location, 0.8, 1)
            .unwrap();

        graph.upsert_edge(john, acme, "employer", 0.9, 1).unwrap();
        graph.upsert_edge(john, sf, "location", 0.8, 1).unwrap();

        let results = graph.follow("John", None, 1);
        assert_eq!(results.len(), 2);

        let employer_results = graph.follow("John", Some("employer"), 1);
        assert_eq!(employer_results.len(), 1);
        assert_eq!(employer_results[0].node_name, "Acme Corp");
    }

    #[test]
    fn test_follow_multi_hop() {
        let mut graph = KnowledgeGraph::new();
        let john = graph
            .upsert_node("John", EntityKind::Person, 0.9, 1)
            .unwrap();
        let acme = graph
            .upsert_node("Acme Corp", EntityKind::Organization, 0.9, 1)
            .unwrap();
        let sf = graph
            .upsert_node("San Francisco", EntityKind::Location, 0.8, 1)
            .unwrap();

        graph.upsert_edge(john, acme, "employer", 0.9, 1).unwrap();
        graph.upsert_edge(acme, sf, "location", 0.8, 1).unwrap();

        // 1-hop from John: only Acme (via employer)
        let hop1 = graph.follow("John", None, 1);
        assert_eq!(hop1.len(), 1);

        // 2-hop from John: Acme + SF
        let hop2 = graph.follow("John", None, 2);
        assert_eq!(hop2.len(), 2);
        let names: Vec<&str> = hop2.iter().map(|r| r.node_name.as_str()).collect();
        assert!(names.contains(&"Acme Corp"));
        assert!(names.contains(&"San Francisco"));
    }

    #[test]
    fn test_follow_nonexistent() {
        let graph = KnowledgeGraph::new();
        let results = graph.follow("Nobody", None, 3);
        assert!(results.is_empty());
    }

    #[test]
    fn test_neighbors() {
        let mut graph = KnowledgeGraph::new();
        let john = graph
            .upsert_node("John", EntityKind::Person, 0.9, 1)
            .unwrap();
        let alice = graph
            .upsert_node("Alice", EntityKind::Person, 0.9, 1)
            .unwrap();

        graph.upsert_edge(john, alice, "colleague", 0.8, 1).unwrap();

        let neighbors = graph.neighbors(john);
        assert_eq!(neighbors.len(), 1);
        assert_eq!(neighbors[0].0.display_name, "Alice");
        assert_eq!(neighbors[0].1.relation_type, "colleague");
    }

    #[test]
    fn test_entities_by_kind() {
        let mut graph = KnowledgeGraph::new();
        graph
            .upsert_node("John", EntityKind::Person, 0.9, 1)
            .unwrap();
        graph
            .upsert_node("Alice", EntityKind::Person, 0.9, 2)
            .unwrap();
        graph
            .upsert_node("Acme", EntityKind::Organization, 0.9, 3)
            .unwrap();

        assert_eq!(graph.entities_by_kind(EntityKind::Person).len(), 2);
        assert_eq!(graph.entities_by_kind(EntityKind::Organization).len(), 1);
    }

    #[test]
    fn test_serialize_roundtrip() {
        let mut graph = KnowledgeGraph::new();
        let john = graph
            .upsert_node("John", EntityKind::Person, 0.9, 1)
            .unwrap();
        let acme = graph
            .upsert_node("Acme", EntityKind::Organization, 0.8, 1)
            .unwrap();
        graph.upsert_edge(john, acme, "employer", 0.9, 1).unwrap();

        let bytes = graph.serialize().unwrap();
        let restored = KnowledgeGraph::deserialize(&bytes).unwrap();

        assert_eq!(restored.node_count(), 2);
        assert_eq!(restored.edge_count(), 1);
        assert!(restored.get_by_name("john").is_some());
    }

    #[test]
    fn test_fuzzy_match() {
        let mut graph = KnowledgeGraph::new();
        graph
            .upsert_node("John Smith", EntityKind::Person, 0.9, 1)
            .unwrap();

        // "John" alone — Jaccard("john" vs "john smith") = 1/2 = 0.5 < 0.7
        // But substring containment boosts to 0.8
        let id = graph
            .upsert_node("John Smith Jr", EntityKind::Person, 0.8, 2)
            .unwrap();
        // Should dedup via substring containment
        assert_eq!(
            graph.node_count(),
            1,
            "substring containment should trigger dedup"
        );
    }
}
