//! Memory Cards: versioned structured memory with cardinality-aware schema.
//!
//! Upgrades animaOS's claims system with memvid-inspired version chains:
//! - Sets → Updates → Extends → Retracts lifecycle
//! - SchemaRegistry enforces Single/Multiple cardinality per (entity, slot)
//! - Full version history per (entity, slot) for audit and rollback
//!
//! Replaces animaOS's 12 hardcoded slot patterns with a configurable registry.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::frame::FrameId;

/// Unique card identifier.
pub type CardId = u64;

/// The kind of memory being stored.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum MemoryKind {
    Fact = 0,
    Preference = 1,
    Event = 2,
    Profile = 3,
    Relationship = 4,
    Goal = 5,
    Other = 6,
}

impl MemoryKind {
    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Fact => "fact",
            Self::Preference => "preference",
            Self::Event => "event",
            Self::Profile => "profile",
            Self::Relationship => "relationship",
            Self::Goal => "goal",
            Self::Other => "other",
        }
    }

    #[must_use]
    pub fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "fact" => Self::Fact,
            "preference" => Self::Preference,
            "event" => Self::Event,
            "profile" => Self::Profile,
            "relationship" => Self::Relationship,
            "goal" => Self::Goal,
            _ => Self::Other,
        }
    }
}

impl std::fmt::Display for MemoryKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

impl Default for MemoryKind {
    fn default() -> Self {
        Self::Fact
    }
}

/// How this card relates to prior versions of the same slot.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum VersionRelation {
    /// First time this slot is being set.
    Sets = 0,
    /// Replaces a previous value entirely.
    Updates = 1,
    /// Adds to existing value (e.g., list of hobbies).
    Extends = 2,
    /// Negates/removes a previous value.
    Retracts = 3,
}

impl Default for VersionRelation {
    fn default() -> Self {
        Self::Sets
    }
}

impl VersionRelation {
    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Sets => "sets",
            Self::Updates => "updates",
            Self::Extends => "extends",
            Self::Retracts => "retracts",
        }
    }

    #[must_use]
    pub fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "updates" => Self::Updates,
            "extends" => Self::Extends,
            "retracts" => Self::Retracts,
            _ => Self::Sets,
        }
    }
}

/// Polarity for preferences and boolean facts.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
#[repr(u8)]
pub enum Polarity {
    Positive = 0,
    Negative = 1,
    Neutral = 2,
}

impl Default for Polarity {
    fn default() -> Self {
        Self::Neutral
    }
}

impl Polarity {
    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Positive => "positive",
            Self::Negative => "negative",
            Self::Neutral => "neutral",
        }
    }

    #[must_use]
    pub fn from_str(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "positive" => Self::Positive,
            "negative" => Self::Negative,
            _ => Self::Neutral,
        }
    }
}

/// A structured memory unit with versioning and provenance.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MemoryCard {
    pub id: CardId,
    pub kind: MemoryKind,
    /// Subject entity: "user", "project_x", "john"
    pub entity: String,
    /// Predicate slot: "employer", "age", "likes"
    pub slot: String,
    /// Object value: "Meta", "28", "dark mode"
    pub value: String,
    #[serde(default)]
    pub polarity: Polarity,
    #[serde(default)]
    pub version: VersionRelation,
    /// Confidence score (0.0-1.0).
    pub confidence: f32,
    /// Source frame that produced this card.
    pub frame_id: FrameId,
    /// Unix timestamp.
    pub created_at: i64,
    /// Whether this card is still active.
    #[serde(default = "default_true")]
    pub active: bool,
    /// Card that superseded this one (if any).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub superseded_by: Option<CardId>,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
struct VersionKey {
    entity: String,
    slot: String,
}

impl VersionKey {
    fn new(entity: &str, slot: &str) -> Self {
        Self {
            entity: entity.to_lowercase(),
            slot: slot.to_lowercase(),
        }
    }
}

fn default_true() -> bool {
    true
}

impl MemoryCard {
    /// Canonical version key for grouping related cards.
    #[must_use]
    fn version_key(&self) -> VersionKey {
        VersionKey::new(&self.entity, &self.slot)
    }
}

// ── Schema Registry ──────────────────────────────────────────────────

/// Cardinality constraint for a (entity_kind, slot) pair.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Cardinality {
    /// At most one active value per (entity, slot). Updates auto-supersede.
    Single,
    /// Multiple active values allowed. Extends appends, Retracts removes specific values.
    Multiple,
}

impl Default for Cardinality {
    fn default() -> Self {
        Self::Single
    }
}

/// Registry of cardinality rules per (entity_pattern, slot).
///
/// Replaces animaOS's 12 hardcoded slot patterns with a dynamic registry.
#[derive(Debug, Clone, Default)]
pub struct SchemaRegistry {
    /// (entity_pattern, slot) → Cardinality
    /// entity_pattern "*" matches any entity.
    rules: HashMap<(String, String), Cardinality>,
}

#[derive(Serialize, Deserialize)]
struct SchemaRuleWire {
    entity_pattern: String,
    slot: String,
    cardinality: Cardinality,
}

impl SchemaRegistry {
    pub fn new() -> Self {
        let mut reg = Self::default();
        // Default rules matching animaOS's existing slot patterns
        reg.set("*", "age", Cardinality::Single);
        reg.set("*", "birthday", Cardinality::Single);
        reg.set("*", "occupation", Cardinality::Single);
        reg.set("*", "employer", Cardinality::Single);
        reg.set("*", "location", Cardinality::Single);
        reg.set("*", "name", Cardinality::Single);
        reg.set("*", "display_name", Cardinality::Single);
        reg.set("*", "username", Cardinality::Single);
        reg.set("*", "gender", Cardinality::Single);
        reg.set("*", "likes", Cardinality::Multiple);
        reg.set("*", "prefers", Cardinality::Multiple);
        reg.set("*", "dislikes", Cardinality::Multiple);
        reg.set("*", "hobbies", Cardinality::Multiple);
        reg
    }

    /// Set cardinality for a (entity_pattern, slot) pair.
    pub fn set(&mut self, entity_pattern: &str, slot: &str, cardinality: Cardinality) {
        self.rules.insert(
            (entity_pattern.to_lowercase(), slot.to_lowercase()),
            cardinality,
        );
    }

    /// Look up cardinality for a given entity and slot.
    /// Checks specific entity first, then wildcard "*".
    /// Defaults to Single if no rule found.
    #[must_use]
    pub fn cardinality(&self, entity: &str, slot: &str) -> Cardinality {
        let entity_lower = entity.to_lowercase();
        let slot_lower = slot.to_lowercase();

        // Check specific entity first
        if let Some(&c) = self.rules.get(&(entity_lower.clone(), slot_lower.clone())) {
            return c;
        }
        // Check wildcard
        if let Some(&c) = self.rules.get(&("*".to_string(), slot_lower)) {
            return c;
        }
        Cardinality::Single
    }
}

// ── Card Store ───────────────────────────────────────────────────────

/// In-memory store for Memory Cards with version tracking.
#[derive(Debug)]
pub struct CardStore {
    cards: Vec<MemoryCard>,
    next_id: CardId,
    /// Index: version_key → card indices (newest last).
    version_index: HashMap<VersionKey, Vec<usize>>,
    /// Schema for cardinality rules.
    pub schema: SchemaRegistry,
}

impl Serialize for SchemaRegistry {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: serde::Serializer,
    {
        let mut rules: Vec<SchemaRuleWire> = self
            .rules
            .iter()
            .map(|((entity_pattern, slot), &cardinality)| SchemaRuleWire {
                entity_pattern: entity_pattern.clone(),
                slot: slot.clone(),
                cardinality,
            })
            .collect();
        rules.sort_by(|left, right| {
            left.entity_pattern
                .cmp(&right.entity_pattern)
                .then(left.slot.cmp(&right.slot))
        });
        rules.serialize(serializer)
    }
}

impl<'de> Deserialize<'de> for SchemaRegistry {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let rules = Vec::<SchemaRuleWire>::deserialize(deserializer)?;
        let mut registry = Self::default();
        for rule in rules {
            registry
                .rules
                .insert((rule.entity_pattern, rule.slot), rule.cardinality);
        }
        Ok(registry)
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CardStoreSnapshot {
    cards: Vec<MemoryCard>,
    schema: SchemaRegistry,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
enum CardStoreWire {
    Legacy(Vec<MemoryCard>),
    Snapshot(CardStoreSnapshot),
}

impl CardStore {
    pub fn new(schema: SchemaRegistry) -> Self {
        Self {
            cards: Vec::new(),
            next_id: 0,
            version_index: HashMap::new(),
            schema,
        }
    }

    /// Insert a new card, enforcing cardinality rules.
    ///
    /// For Single cardinality with Sets/Updates: auto-supersedes the previous active card.
    /// For Single cardinality with Retracts: deactivates the matching card.
    /// For Multiple cardinality with Extends: appends.
    /// For Multiple cardinality with Retracts: deactivates a specific value match.
    pub fn put(&mut self, mut card: MemoryCard) -> CardId {
        card.superseded_by = None;
        card.active = true;

        let vk = card.version_key();
        let cardinality = self.schema.cardinality(&card.entity, &card.slot);

        if !matches!(card.version, VersionRelation::Retracts) {
            if let Some(existing_id) = self.find_active_duplicate(&card) {
                return existing_id;
            }
        }

        card.id = self.next_id;
        self.next_id += 1;

        match (cardinality, card.version) {
            (Cardinality::Single, VersionRelation::Sets | VersionRelation::Updates) => {
                // Supersede all previous active cards for this version key
                if let Some(indices) = self.version_index.get(&vk) {
                    for &idx in indices {
                        if self.cards[idx].active {
                            self.cards[idx].active = false;
                            self.cards[idx].superseded_by = Some(card.id);
                        }
                    }
                }
            }
            (Cardinality::Single, VersionRelation::Retracts) => {
                // Deactivate matching cards
                if let Some(indices) = self.version_index.get(&vk) {
                    for &idx in indices {
                        if self.cards[idx].active {
                            self.cards[idx].active = false;
                            self.cards[idx].superseded_by = Some(card.id);
                        }
                    }
                }
                card.active = false; // Retraction itself is not "active"
            }
            (Cardinality::Multiple, VersionRelation::Retracts) => {
                // Deactivate only the specific value match
                if let Some(indices) = self.version_index.get(&vk) {
                    for &idx in indices {
                        if self.cards[idx].active
                            && cards_share_active_state(&self.cards[idx], &card)
                        {
                            self.cards[idx].active = false;
                            self.cards[idx].superseded_by = Some(card.id);
                        }
                    }
                }
                card.active = false;
            }
            // Multiple + Sets/Updates/Extends: just append
            (Cardinality::Multiple, _) => {}
            // Single + Extends: treat as Sets (only one value allowed)
            (Cardinality::Single, VersionRelation::Extends) => {
                if let Some(indices) = self.version_index.get(&vk) {
                    for &idx in indices {
                        if self.cards[idx].active {
                            self.cards[idx].active = false;
                            self.cards[idx].superseded_by = Some(card.id);
                        }
                    }
                }
            }
        }

        let id = card.id;
        let idx = self.cards.len();
        self.version_index.entry(vk).or_default().push(idx);
        self.cards.push(card);
        id
    }

    /// Get the current active value(s) for a given entity and slot.
    pub fn get_current(&self, entity: &str, slot: &str) -> Vec<&MemoryCard> {
        let vk = VersionKey::new(entity, slot);
        self.version_index
            .get(&vk)
            .map(|indices| {
                indices
                    .iter()
                    .filter_map(|&idx| {
                        let c = &self.cards[idx];
                        if c.active {
                            Some(c)
                        } else {
                            None
                        }
                    })
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Get the full version history for a given entity and slot (oldest first).
    pub fn get_history(&self, entity: &str, slot: &str) -> Vec<&MemoryCard> {
        let vk = VersionKey::new(entity, slot);
        self.version_index
            .get(&vk)
            .map(|indices| indices.iter().map(|&idx| &self.cards[idx]).collect())
            .unwrap_or_default()
    }

    /// Get all active cards for a given entity.
    pub fn get_by_entity(&self, entity: &str) -> Vec<&MemoryCard> {
        let entity_key = entity.to_lowercase();
        self.version_index
            .iter()
            .filter(|(k, _)| k.entity == entity_key)
            .flat_map(|(_, indices)| {
                indices.iter().filter_map(|&idx| {
                    let c = &self.cards[idx];
                    if c.active {
                        Some(c)
                    } else {
                        None
                    }
                })
            })
            .collect()
    }

    /// Get a card by ID.
    #[must_use]
    pub fn get(&self, id: CardId) -> Option<&MemoryCard> {
        self.cards.iter().find(|c| c.id == id)
    }

    /// Total cards (including inactive).
    #[must_use]
    pub fn len(&self) -> usize {
        self.cards.len()
    }

    /// Number of active cards.
    #[must_use]
    pub fn active_count(&self) -> usize {
        self.cards.iter().filter(|c| c.active).count()
    }

    /// Iterate over all cards in insertion order.
    pub fn iter(&self) -> impl Iterator<Item = &MemoryCard> {
        self.cards.iter()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.cards.is_empty()
    }

    /// Serialize all cards to JSON bytes.
    pub fn serialize(&self) -> crate::Result<Vec<u8>> {
        serde_json::to_vec(&CardStoreSnapshot {
            cards: self.cards.clone(),
            schema: self.schema.clone(),
        })
        .map_err(|e| crate::Error::Serialization(e.to_string()))
    }

    /// Deserialize cards from JSON bytes and rebuild indices.
    pub fn deserialize(bytes: &[u8], schema: SchemaRegistry) -> crate::Result<Self> {
        let (cards, schema) = match serde_json::from_slice(bytes)
            .map_err(|e| crate::Error::Serialization(e.to_string()))?
        {
            CardStoreWire::Legacy(cards) => (cards, schema),
            CardStoreWire::Snapshot(snapshot) => (snapshot.cards, snapshot.schema),
        };

        let mut store = Self {
            next_id: cards.iter().map(|c| c.id).max().unwrap_or(0) + 1,
            cards: Vec::new(),
            version_index: HashMap::new(),
            schema,
        };

        for card in cards {
            let idx = store.cards.len();
            let vk = card.version_key();
            store.version_index.entry(vk).or_default().push(idx);
            store.cards.push(card);
        }

        Ok(store)
    }

    fn find_active_duplicate(&self, card: &MemoryCard) -> Option<CardId> {
        self.version_index.get(&card.version_key()).and_then(|indices| {
            indices.iter().find_map(|&idx| {
                let existing = &self.cards[idx];
                if existing.active && cards_share_active_state(existing, card) {
                    Some(existing.id)
                } else {
                    None
                }
            })
        })
    }
}

fn cards_share_active_state(existing: &MemoryCard, incoming: &MemoryCard) -> bool {
    existing.kind == incoming.kind
        && existing.value == incoming.value
        && existing.polarity == incoming.polarity
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_card(entity: &str, slot: &str, value: &str, version: VersionRelation) -> MemoryCard {
        MemoryCard {
            id: 0,
            kind: MemoryKind::Fact,
            entity: entity.into(),
            slot: slot.into(),
            value: value.into(),
            polarity: Polarity::Neutral,
            version,
            confidence: 1.0,
            frame_id: 0,
            created_at: 0,
            active: true,
            superseded_by: None,
        }
    }

    #[test]
    fn test_single_cardinality_supersede() {
        let mut store = CardStore::new(SchemaRegistry::new());

        // "employer" is Single cardinality
        let id1 = store.put(make_card(
            "user",
            "employer",
            "Google",
            VersionRelation::Sets,
        ));
        let id2 = store.put(make_card(
            "user",
            "employer",
            "Meta",
            VersionRelation::Updates,
        ));

        let current = store.get_current("user", "employer");
        assert_eq!(current.len(), 1);
        assert_eq!(current[0].value, "Meta");

        // Old card should be superseded
        let old = store.get(id1).unwrap();
        assert!(!old.active);
        assert_eq!(old.superseded_by, Some(id2));
    }

    #[test]
    fn test_multiple_cardinality_extends() {
        let mut store = CardStore::new(SchemaRegistry::new());

        // "likes" is Multiple cardinality
        store.put(make_card("user", "likes", "coffee", VersionRelation::Sets));
        store.put(make_card("user", "likes", "tea", VersionRelation::Extends));

        let current = store.get_current("user", "likes");
        assert_eq!(current.len(), 2);
        let values: Vec<&str> = current.iter().map(|c| c.value.as_str()).collect();
        assert!(values.contains(&"coffee"));
        assert!(values.contains(&"tea"));
    }

    #[test]
    fn test_retract_single() {
        let mut store = CardStore::new(SchemaRegistry::new());

        store.put(make_card(
            "user",
            "employer",
            "Google",
            VersionRelation::Sets,
        ));
        store.put(make_card(
            "user",
            "employer",
            "Google",
            VersionRelation::Retracts,
        ));

        let current = store.get_current("user", "employer");
        assert!(
            current.is_empty(),
            "retraction should leave no active cards"
        );
    }

    #[test]
    fn test_retract_multiple_specific_value() {
        let mut store = CardStore::new(SchemaRegistry::new());

        store.put(make_card("user", "likes", "coffee", VersionRelation::Sets));
        store.put(make_card("user", "likes", "tea", VersionRelation::Extends));
        store.put(make_card(
            "user",
            "likes",
            "coffee",
            VersionRelation::Retracts,
        ));

        let current = store.get_current("user", "likes");
        assert_eq!(current.len(), 1);
        assert_eq!(current[0].value, "tea");
    }

    #[test]
    fn test_retract_multiple_matches_active_state_not_value_only() {
        let mut store = CardStore::new(SchemaRegistry::new());

        let mut positive = make_card("user", "likes", "coffee", VersionRelation::Sets);
        positive.polarity = Polarity::Positive;
        let positive_id = store.put(positive);

        let mut negative = make_card("user", "likes", "coffee", VersionRelation::Extends);
        negative.polarity = Polarity::Negative;
        let negative_id = store.put(negative);

        let mut retract_negative = make_card("user", "likes", "coffee", VersionRelation::Retracts);
        retract_negative.polarity = Polarity::Negative;
        store.put(retract_negative);

        assert!(store.get(positive_id).unwrap().active);
        assert!(!store.get(negative_id).unwrap().active);
        let current = store.get_current("user", "likes");
        assert_eq!(current.len(), 1);
        assert_eq!(current[0].id, positive_id);
        assert_eq!(current[0].polarity, Polarity::Positive);
    }

    #[test]
    fn test_version_history() {
        let mut store = CardStore::new(SchemaRegistry::new());

        store.put(make_card(
            "user",
            "employer",
            "Google",
            VersionRelation::Sets,
        ));
        store.put(make_card(
            "user",
            "employer",
            "Meta",
            VersionRelation::Updates,
        ));
        store.put(make_card(
            "user",
            "employer",
            "Anthropic",
            VersionRelation::Updates,
        ));

        let history = store.get_history("user", "employer");
        assert_eq!(history.len(), 3);
        assert_eq!(history[0].value, "Google");
        assert_eq!(history[1].value, "Meta");
        assert_eq!(history[2].value, "Anthropic");
    }

    #[test]
    fn test_get_by_entity() {
        let mut store = CardStore::new(SchemaRegistry::new());

        store.put(make_card("user", "employer", "Meta", VersionRelation::Sets));
        store.put(make_card("user", "age", "28", VersionRelation::Sets));
        store.put(make_card(
            "project_x",
            "status",
            "active",
            VersionRelation::Sets,
        ));

        let user_cards = store.get_by_entity("user");
        assert_eq!(user_cards.len(), 2);

        let project_cards = store.get_by_entity("project_x");
        assert_eq!(project_cards.len(), 1);
    }

    #[test]
    fn test_custom_schema() {
        let mut schema = SchemaRegistry::new();
        schema.set("user", "emails", Cardinality::Multiple);

        let mut store = CardStore::new(schema);

        store.put(make_card(
            "user",
            "emails",
            "a@example.com",
            VersionRelation::Sets,
        ));
        store.put(make_card(
            "user",
            "emails",
            "b@example.com",
            VersionRelation::Extends,
        ));

        let current = store.get_current("user", "emails");
        assert_eq!(current.len(), 2);
    }

    #[test]
    fn test_serialize_roundtrip() {
        let mut store = CardStore::new(SchemaRegistry::new());
        store.put(make_card(
            "user",
            "employer",
            "Google",
            VersionRelation::Sets,
        ));
        store.put(make_card(
            "user",
            "employer",
            "Meta",
            VersionRelation::Updates,
        ));

        let bytes = store.serialize().unwrap();
        let restored = CardStore::deserialize(&bytes, SchemaRegistry::new()).unwrap();

        assert_eq!(restored.len(), 2);
        let current = restored.get_current("user", "employer");
        assert_eq!(current.len(), 1);
        assert_eq!(current[0].value, "Meta");
    }

    #[test]
    fn test_single_cardinality_dedups_repeated_active_insert() {
        let mut store = CardStore::new(SchemaRegistry::new());
        let card = make_card("user", "employer", "Meta", VersionRelation::Sets);

        let first_id = store.put(card.clone());
        let second_id = store.put(card);

        assert_eq!(first_id, second_id);
        assert_eq!(store.len(), 1);
        assert_eq!(store.active_count(), 1);
        assert_eq!(store.get_current("user", "employer").len(), 1);
    }

    #[test]
    fn test_multiple_cardinality_dedups_same_active_state() {
        let mut store = CardStore::new(SchemaRegistry::new());
        let card = make_card("user", "likes", "coffee", VersionRelation::Extends);

        let first_id = store.put(card.clone());
        let second_id = store.put(card);

        assert_eq!(first_id, second_id);
        assert_eq!(store.len(), 1);
        assert_eq!(store.active_count(), 1);
        assert_eq!(store.get_current("user", "likes").len(), 1);
    }

    #[test]
    fn test_multiple_cardinality_dedups_same_active_state_across_version_relations() {
        let mut store = CardStore::new(SchemaRegistry::new());

        let first_id = store.put(make_card("user", "likes", "coffee", VersionRelation::Sets));
        let second_id = store.put(make_card("user", "likes", "coffee", VersionRelation::Extends));

        assert_eq!(first_id, second_id);
        assert_eq!(store.len(), 1);
        assert_eq!(store.active_count(), 1);
        assert_eq!(store.get_current("user", "likes")[0].value, "coffee");
    }

    #[test]
    fn test_single_cardinality_dedups_same_active_value_across_version_relations() {
        let mut store = CardStore::new(SchemaRegistry::new());

        let first_id = store.put(make_card("user", "employer", "Meta", VersionRelation::Sets));
        let second_id =
            store.put(make_card("user", "employer", "Meta", VersionRelation::Updates));
        let third_id =
            store.put(make_card("user", "employer", "Meta", VersionRelation::Extends));

        assert_eq!(first_id, second_id);
        assert_eq!(second_id, third_id);
        assert_eq!(store.len(), 1);
        assert_eq!(store.active_count(), 1);
        assert_eq!(store.get_current("user", "employer")[0].value, "Meta");
    }

    #[test]
    fn test_put_allows_reinsert_after_retraction() {
        let mut store = CardStore::new(SchemaRegistry::new());

        let first_id = store.put(make_card("user", "likes", "coffee", VersionRelation::Sets));
        let retraction_id = store.put(make_card("user", "likes", "coffee", VersionRelation::Retracts));
        let second_id = store.put(make_card("user", "likes", "coffee", VersionRelation::Extends));

        assert_eq!(first_id, 0);
        assert_eq!(retraction_id, 1);
        assert_eq!(second_id, 2);
        assert_eq!(store.len(), 3);
        assert_eq!(store.active_count(), 1);
        assert_eq!(store.get_current("user", "likes")[0].id, second_id);
    }

    #[test]
    fn test_version_key_handles_delimiter_bearing_entity_and_slot() {
        let mut store = CardStore::new(SchemaRegistry::new());

        store.put(make_card("foo:bar", "baz", "left", VersionRelation::Sets));
        store.put(make_card("foo", "bar:baz", "right", VersionRelation::Sets));

        let left = store.get_current("foo:bar", "baz");
        let right = store.get_current("foo", "bar:baz");

        assert_eq!(left.len(), 1);
        assert_eq!(right.len(), 1);
        assert_eq!(left[0].value, "left");
        assert_eq!(right[0].value, "right");
        assert_eq!(store.len(), 2);
    }

    #[test]
    fn test_serialize_roundtrip_preserves_schema() {
        let mut schema = SchemaRegistry::new();
        schema.set("user", "emails", Cardinality::Multiple);

        let mut store = CardStore::new(schema);
        store.put(make_card(
            "user",
            "emails",
            "a@example.com",
            VersionRelation::Sets,
        ));

        let bytes = store.serialize().unwrap();
        let restored = CardStore::deserialize(&bytes, SchemaRegistry::new()).unwrap();

        assert_eq!(
            restored.schema.cardinality("user", "emails"),
            Cardinality::Multiple
        );
    }

    #[test]
    fn test_put_clears_caller_supplied_superseded_by_on_new_active_card() {
        let mut store = CardStore::new(SchemaRegistry::new());
        let mut card = make_card("user", "employer", "Meta", VersionRelation::Sets);
        card.superseded_by = Some(999);

        let id = store.put(card);

        let stored = store.get(id).unwrap();
        assert!(stored.active);
        assert_eq!(stored.superseded_by, None);
    }
}
