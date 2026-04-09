use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::cards::{CardStore, MemoryCard};
use crate::frame::FrameId;
use crate::graph::{EntityKind, KnowledgeGraph};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SlotValueState {
    pub slot: String,
    pub values: Vec<String>,
    pub supporting_frame_ids: Vec<FrameId>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConnectedEntityState {
    pub relation_type: String,
    pub entity_name: String,
    pub entity_kind: EntityKind,
    pub supporting_frame_ids: Vec<FrameId>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EntityState {
    pub entity: String,
    pub slots: Vec<SlotValueState>,
    pub connected_entities: Vec<ConnectedEntityState>,
    pub supporting_frame_ids: Vec<FrameId>,
}

#[derive(Debug)]
struct EntityStateBuilder {
    entity: String,
    slots: BTreeMap<String, SlotAccumulator>,
    connected_entities: BTreeMap<(String, String, u8), ConnectedEntityAccumulator>,
    supporting_frame_ids: BTreeSet<FrameId>,
}

#[derive(Debug, Default)]
struct SlotAccumulator {
    values: BTreeSet<String>,
    supporting_frame_ids: BTreeSet<FrameId>,
}

#[derive(Debug)]
struct ConnectedEntityAccumulator {
    relation_type: String,
    entity_name: String,
    entity_kind: EntityKind,
    supporting_frame_ids: BTreeSet<FrameId>,
}

impl EntityStateBuilder {
    fn new(entity: impl Into<String>) -> Self {
        Self {
            entity: entity.into(),
            slots: BTreeMap::new(),
            connected_entities: BTreeMap::new(),
            supporting_frame_ids: BTreeSet::new(),
        }
    }

    fn add_active_slot_value(
        &mut self,
        slot: impl Into<String>,
        value: impl Into<String>,
        supporting_frame_ids: impl IntoIterator<Item = FrameId>,
    ) {
        let slot = slot.into();
        let value = value.into();
        let accumulator = self.slots.entry(slot).or_default();

        accumulator.values.insert(value);
        for frame_id in supporting_frame_ids {
            accumulator.supporting_frame_ids.insert(frame_id);
            self.supporting_frame_ids.insert(frame_id);
        }
    }

    fn add_connected_entity(
        &mut self,
        relation_type: impl Into<String>,
        entity_name: impl Into<String>,
        entity_kind: EntityKind,
        supporting_frame_id: FrameId,
    ) {
        let relation_type = relation_type.into();
        let entity_name = entity_name.into();
        let key = (
            relation_type.to_lowercase(),
            entity_name.to_lowercase(),
            entity_kind as u8,
        );
        let accumulator = self
            .connected_entities
            .entry(key)
            .or_insert_with(|| ConnectedEntityAccumulator {
                relation_type,
                entity_name,
                entity_kind,
                supporting_frame_ids: BTreeSet::new(),
            });

        accumulator.supporting_frame_ids.insert(supporting_frame_id);
        self.supporting_frame_ids.insert(supporting_frame_id);
    }

    fn build(self) -> EntityState {
        let slots = self
            .slots
            .into_iter()
            .map(|(slot, accumulator)| SlotValueState {
                slot,
                values: accumulator.values.into_iter().collect(),
                supporting_frame_ids: accumulator.supporting_frame_ids.into_iter().collect(),
            })
            .collect();
        let connected_entities = self
            .connected_entities
            .into_iter()
            .map(|(_, accumulator)| ConnectedEntityState {
                relation_type: accumulator.relation_type,
                entity_name: accumulator.entity_name,
                entity_kind: accumulator.entity_kind,
                supporting_frame_ids: accumulator.supporting_frame_ids.into_iter().collect(),
            })
            .collect();

        EntityState {
            entity: self.entity,
            slots,
            connected_entities,
            supporting_frame_ids: self.supporting_frame_ids.into_iter().collect(),
        }
    }
}

pub fn entity_state_from_cards(cards: &CardStore, entity: &str) -> EntityState {
    entity_state_from_cards_inner(cards, entity).build()
}

pub fn entity_state_from_cards_and_graph(
    cards: &CardStore,
    graph: &KnowledgeGraph,
    entity: &str,
) -> EntityState {
    let mut builder = entity_state_from_cards_inner(cards, entity);

    if let Some(node) = graph.get_by_name(entity) {
        for (neighbor, edge) in graph.neighbors(node.id) {
            builder.add_connected_entity(
                edge.relation_type.clone(),
                neighbor.display_name.clone(),
                neighbor.kind,
                edge.frame_id,
            );
        }
    }

    builder.build()
}

fn entity_state_from_cards_inner(cards: &CardStore, entity: &str) -> EntityStateBuilder {
    let mut builder = EntityStateBuilder::new(entity);

    for card in cards.get_by_entity(entity) {
        builder.add_active_slot_value(&card.slot, card.value.clone(), [card.frame_id]);
    }

    builder
}

pub fn slot_history(cards: &CardStore, entity: &str, slot: &str) -> Vec<MemoryCard> {
    cards.get_history(entity, slot).into_iter().cloned().collect()
}

#[cfg(test)]
mod tests {
    use crate::cards::{CardStore, MemoryCard, MemoryKind, Polarity, SchemaRegistry, VersionRelation};
    use crate::graph::{EntityKind, KnowledgeGraph};

    fn make_card(
        entity: &str,
        slot: &str,
        value: &str,
        version: VersionRelation,
        frame_id: u64,
    ) -> MemoryCard {
        MemoryCard {
            id: 0,
            kind: MemoryKind::Fact,
            entity: entity.into(),
            slot: slot.into(),
            value: value.into(),
            polarity: Polarity::Neutral,
            version,
            confidence: 1.0,
            frame_id,
            created_at: frame_id as i64,
            active: true,
            superseded_by: None,
        }
    }

    #[test]
    fn entity_state_groups_active_slot_values_for_single_and_multiple_slots() {
        let mut builder = super::EntityStateBuilder::new("user");

        builder.add_active_slot_value("likes", "jazz", [9, 2, 2]);
        builder.add_active_slot_value("employer", "OpenAI", [4]);
        builder.add_active_slot_value("likes", "coffee", [7, 4]);

        let state = builder.build();

        assert_eq!(state.entity, "user");
        assert_eq!(state.slots.len(), 2);
        assert_eq!(state.slots[0].slot, "employer");
        assert_eq!(state.slots[0].values, vec!["OpenAI"]);
        assert_eq!(state.slots[0].supporting_frame_ids, vec![4]);
        assert_eq!(state.slots[1].slot, "likes");
        assert_eq!(state.slots[1].values, vec!["coffee", "jazz"]);
        assert_eq!(state.slots[1].supporting_frame_ids, vec![2, 4, 7, 9]);
        assert!(state.connected_entities.is_empty());
    }

    #[test]
    fn entity_state_collects_supporting_frame_ids_without_duplicates() {
        let mut builder = super::EntityStateBuilder::new("user");

        builder.add_active_slot_value("likes", "tea", [9, 2, 9]);
        builder.add_active_slot_value("likes", "coffee", [7, 2]);
        builder.add_active_slot_value("likes", "coffee", [2, 7, 9]);

        let state = builder.build();

        assert_eq!(state.slots.len(), 1);
        assert_eq!(state.slots[0].values, vec!["coffee", "tea"]);
        assert_eq!(state.slots[0].supporting_frame_ids, vec![2, 7, 9]);
        assert_eq!(state.supporting_frame_ids, vec![2, 7, 9]);
        assert!(state.connected_entities.is_empty());
    }

    #[test]
    fn slot_history_returns_versions_oldest_to_newest_for_entity_slot() {
        let mut cards = CardStore::new(SchemaRegistry::new());

        let mut set = make_card("user", "employer", "Google", VersionRelation::Sets, 1);
        set.created_at = 300;
        cards.put(set);

        let mut update = make_card(
            "user",
            "employer",
            "Meta",
            VersionRelation::Updates,
            2,
        );
        update.created_at = 100;
        cards.put(update);

        let mut retract = make_card(
            "user",
            "employer",
            "Meta",
            VersionRelation::Retracts,
            3,
        );
        retract.created_at = 200;
        cards.put(retract);

        let history = super::slot_history(&cards, "user", "employer");

        assert_eq!(history.len(), 3);
        assert_eq!(history[0].value, "Google");
        assert_eq!(history[0].version, VersionRelation::Sets);
        assert_eq!(history[1].value, "Meta");
        assert_eq!(history[1].version, VersionRelation::Updates);
        assert_eq!(history[2].value, "Meta");
        assert_eq!(history[2].version, VersionRelation::Retracts);
    }

    #[test]
    fn entity_state_from_cards_collapses_multiple_active_cards_for_one_slot() {
        let mut cards = CardStore::new(SchemaRegistry::new());

        cards.put(make_card("user", "likes", "zebra", VersionRelation::Sets, 30));
        cards.put(make_card("user", "likes", "coffee", VersionRelation::Extends, 10));
        cards.put(make_card("user", "likes", "alpha", VersionRelation::Extends, 20));

        let state = super::entity_state_from_cards(&cards, "user");

        assert_eq!(state.entity, "user");
        assert_eq!(state.slots.len(), 1);
        assert_eq!(state.slots[0].slot, "likes");
        assert_eq!(state.slots[0].values, vec!["alpha", "coffee", "zebra"]);
        assert_eq!(state.slots[0].supporting_frame_ids, vec![10, 20, 30]);
        assert_eq!(state.supporting_frame_ids, vec![10, 20, 30]);
        assert!(state.connected_entities.is_empty());
    }

    #[test]
    fn entity_state_includes_connected_entities_from_graph_edges() {
        let mut cards = CardStore::new(SchemaRegistry::new());
        cards.put(make_card("user", "likes", "coffee", VersionRelation::Sets, 5));

        let mut graph = KnowledgeGraph::new();
        let user = graph
            .upsert_node("user", EntityKind::Person, 0.9, 1)
            .unwrap();
        let openai = graph
            .upsert_node("OpenAI", EntityKind::Organization, 0.9, 2)
            .unwrap();
        let alice = graph
            .upsert_node("Alice", EntityKind::Person, 0.9, 3)
            .unwrap();

        graph.upsert_edge(user, openai, "employer", 0.9, 20).unwrap();
        graph.upsert_edge(user, alice, "colleague", 0.8, 10).unwrap();

        let state = super::entity_state_from_cards_and_graph(&cards, &graph, "user");

        assert_eq!(state.entity, "user");
        assert_eq!(state.slots.len(), 1);
        assert_eq!(state.slots[0].slot, "likes");
        assert_eq!(state.slots[0].values, vec!["coffee"]);
        assert_eq!(state.connected_entities.len(), 2);
        assert_eq!(state.connected_entities[0].relation_type, "colleague");
        assert_eq!(state.connected_entities[0].entity_name, "Alice");
        assert_eq!(state.connected_entities[0].entity_kind, EntityKind::Person);
        assert_eq!(state.connected_entities[0].supporting_frame_ids, vec![10]);
        assert_eq!(state.connected_entities[1].relation_type, "employer");
        assert_eq!(state.connected_entities[1].entity_name, "OpenAI");
        assert_eq!(state.connected_entities[1].entity_kind, EntityKind::Organization);
        assert_eq!(state.connected_entities[1].supporting_frame_ids, vec![20]);
        assert_eq!(state.supporting_frame_ids, vec![5, 10, 20]);
    }

    #[test]
    fn entity_state_handles_missing_graph_node_gracefully() {
        let mut cards = CardStore::new(SchemaRegistry::new());
        cards.put(make_card("user", "likes", "coffee", VersionRelation::Sets, 5));

        let graph = KnowledgeGraph::new();

        let state = super::entity_state_from_cards_and_graph(&cards, &graph, "user");

        assert_eq!(state.entity, "user");
        assert_eq!(state.slots.len(), 1);
        assert_eq!(state.slots[0].slot, "likes");
        assert_eq!(state.slots[0].values, vec!["coffee"]);
        assert_eq!(state.slots[0].supporting_frame_ids, vec![5]);
        assert_eq!(state.supporting_frame_ids, vec![5]);
        assert!(state.connected_entities.is_empty());
    }

    #[test]
    fn entity_state_orders_connected_entities_by_relation_then_entity_name() {
        let cards = CardStore::new(SchemaRegistry::new());

        let mut graph = KnowledgeGraph::new();
        let user = graph
            .upsert_node("user", EntityKind::Person, 0.9, 1)
            .unwrap();
        let zed = graph
            .upsert_node("Zed", EntityKind::Person, 0.9, 2)
            .unwrap();
        let alice = graph
            .upsert_node("Alice", EntityKind::Person, 0.9, 3)
            .unwrap();
        let openai = graph
            .upsert_node("OpenAI", EntityKind::Organization, 0.9, 4)
            .unwrap();

        graph.upsert_edge(user, zed, "colleague", 0.9, 30).unwrap();
        graph.upsert_edge(user, alice, "colleague", 0.9, 10).unwrap();
        graph.upsert_edge(user, openai, "employer", 0.9, 20).unwrap();

        let state = super::entity_state_from_cards_and_graph(&cards, &graph, "user");

        assert_eq!(state.connected_entities.len(), 3);
        assert_eq!(state.connected_entities[0].relation_type, "colleague");
        assert_eq!(state.connected_entities[0].entity_name, "Alice");
        assert_eq!(state.connected_entities[0].supporting_frame_ids, vec![10]);
        assert_eq!(state.connected_entities[1].relation_type, "colleague");
        assert_eq!(state.connected_entities[1].entity_name, "Zed");
        assert_eq!(state.connected_entities[1].supporting_frame_ids, vec![30]);
        assert_eq!(state.connected_entities[2].relation_type, "employer");
        assert_eq!(state.connected_entities[2].entity_name, "OpenAI");
        assert_eq!(state.connected_entities[2].supporting_frame_ids, vec![20]);
    }
}
