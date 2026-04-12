use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::cards::{CardStore, Cardinality, MemoryCard};
use crate::frame::{Frame, FrameKind, FrameMetadata, FrameSource, FrameStatus, FrameStore};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IntegritySeverity {
    Info,
    Warning,
    Error,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum IntegrityIssueKind {
    FrameChecksumMismatch,
    DuplicateActiveFrame,
    DuplicateActiveCard,
    InvalidSupersession,
    OrphanedGraphEdge,
    CapsuleFormatInvalid,
    CapsuleEncryptionRequired,
    CapsuleFooterChecksumMismatch,
    CapsuleSectionChecksumMismatch,
    CapsuleSectionOutOfBounds,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IntegrityIssue {
    pub kind: IntegrityIssueKind,
    pub severity: IntegritySeverity,
    pub message: String,
    pub record_ids: Vec<u64>,
    pub repair_hint: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CoreStats {
    pub frame_count: usize,
    pub active_frame_count: usize,
    pub superseded_frame_count: usize,
    pub card_count: usize,
    pub active_card_count: usize,
    pub superseded_card_count: usize,
    pub graph_node_count: usize,
    pub graph_edge_count: usize,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct IntegrityReport {
    pub ok: bool,
    pub issues: Vec<IntegrityIssue>,
    pub stats: CoreStats,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapsuleIntegrityMetadata {
    pub version: u8,
    pub encrypted: bool,
    pub sections: Vec<crate::capsule::CapsuleSectionInfo>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapsuleIntegrityReport {
    pub ok: bool,
    pub issues: Vec<IntegrityIssue>,
    pub stats: CoreStats,
    pub capsule: CapsuleIntegrityMetadata,
}

#[must_use]
pub fn verify_capsule_integrity(raw: &[u8], password: Option<&[u8]>) -> CapsuleIntegrityReport {
    let report = crate::capsule::verify_capsule(raw, password);
    let issues = report
        .issues
        .into_iter()
        .map(|issue| IntegrityIssue {
            kind: match issue.kind {
                crate::capsule::CapsuleVerificationIssueKind::FooterChecksumMismatch => {
                    IntegrityIssueKind::CapsuleFooterChecksumMismatch
                }
                crate::capsule::CapsuleVerificationIssueKind::SectionChecksumMismatch => {
                    IntegrityIssueKind::CapsuleSectionChecksumMismatch
                }
                crate::capsule::CapsuleVerificationIssueKind::SectionOutOfBounds => {
                    IntegrityIssueKind::CapsuleSectionOutOfBounds
                }
                crate::capsule::CapsuleVerificationIssueKind::SectionDecryptionFailed
                | crate::capsule::CapsuleVerificationIssueKind::SectionDecompressionFailed
                | crate::capsule::CapsuleVerificationIssueKind::SectionTooLarge
                | crate::capsule::CapsuleVerificationIssueKind::SectionOffsetOverflow
                | crate::capsule::CapsuleVerificationIssueKind::DuplicateSectionKind => {
                    IntegrityIssueKind::CapsuleFormatInvalid
                }
                crate::capsule::CapsuleVerificationIssueKind::MissingPassword => {
                    IntegrityIssueKind::CapsuleEncryptionRequired
                }
                crate::capsule::CapsuleVerificationIssueKind::CapsuleTooSmall
                | crate::capsule::CapsuleVerificationIssueKind::InvalidMagic
                | crate::capsule::CapsuleVerificationIssueKind::HeaderReadFailed
                | crate::capsule::CapsuleVerificationIssueKind::UnsupportedVersion
                | crate::capsule::CapsuleVerificationIssueKind::DirectoryReadFailed => {
                    IntegrityIssueKind::CapsuleFormatInvalid
                }
            },
            severity: issue.severity,
            message: issue.message,
            record_ids: issue
                .section
                .map(|section| vec![section as u64])
                .unwrap_or_default(),
            repair_hint: match issue.kind {
                crate::capsule::CapsuleVerificationIssueKind::FooterChecksumMismatch => {
                    Some("re-export the capsule and avoid mutating bytes after writing".into())
                }
                crate::capsule::CapsuleVerificationIssueKind::SectionChecksumMismatch => {
                    Some("re-export the capsule because one section payload was modified".into())
                }
                crate::capsule::CapsuleVerificationIssueKind::SectionOutOfBounds => {
                    Some("re-export the capsule because a section directory entry is invalid".into())
                }
                crate::capsule::CapsuleVerificationIssueKind::MissingPassword => {
                    Some("provide the capsule password before verifying encrypted content".into())
                }
                _ => None,
            },
        })
        .collect();

    CapsuleIntegrityReport {
        ok: report.ok,
        issues,
        stats: CoreStats::default(),
        capsule: CapsuleIntegrityMetadata {
            version: report.version,
            encrypted: report.encrypted,
            sections: report.sections,
        },
    }
}

#[must_use]
pub fn scan_frame_store(store: &FrameStore) -> IntegrityReport {
    let mut issues = Vec::new();
    let mut stats = CoreStats::default();
    let mut active_groups: BTreeMap<String, Vec<u64>> = BTreeMap::new();

    for frame in store.iter() {
        stats.frame_count += 1;
        if frame.is_active() {
            stats.active_frame_count += 1;
            active_groups
                .entry(active_frame_identity(frame))
                .or_default()
                .push(frame.id);
        }
        if frame.status == FrameStatus::Superseded {
            stats.superseded_frame_count += 1;
        }
        if !frame.verify_checksum() {
            issues.push(IntegrityIssue {
                kind: IntegrityIssueKind::FrameChecksumMismatch,
                severity: IntegritySeverity::Error,
                message: format!("frame {} checksum mismatch", frame.id),
                record_ids: vec![frame.id],
                repair_hint: Some("recompute the frame checksum from its content".into()),
            });
        }
    }

    for record_ids in active_groups.into_values() {
        if record_ids.len() > 1 {
            issues.push(IntegrityIssue {
                kind: IntegrityIssueKind::DuplicateActiveFrame,
                severity: IntegritySeverity::Error,
                message: "duplicate active frames share the same identity".into(),
                record_ids,
                repair_hint: Some(
                    "supersede or delete duplicate active frames so only one active copy remains"
                        .into(),
                ),
            });
        }
    }

    IntegrityReport {
        ok: issues.is_empty(),
        issues,
        stats,
    }
}

#[must_use]
pub fn scan_card_store(store: &CardStore) -> IntegrityReport {
    let mut stats = CoreStats::default();
    let mut issues = Vec::new();
    let mut single_groups: BTreeMap<(String, String), Vec<&MemoryCard>> = BTreeMap::new();
    let mut multiple_groups: BTreeMap<(String, String), BTreeMap<String, Vec<u64>>> = BTreeMap::new();

    for card in store.iter() {
        stats.card_count += 1;
        if card.active {
            stats.active_card_count += 1;
            let key = (card.entity.to_lowercase(), card.slot.to_lowercase());
            match store.schema.cardinality(&card.entity, &card.slot) {
                Cardinality::Single => {
                    single_groups.entry(key).or_default().push(card);
                }
                Cardinality::Multiple => {
                    multiple_groups
                        .entry(key)
                        .or_default()
                        .entry(card.value.clone())
                        .or_default()
                        .push(card.id);
                }
            }
        }
        if card.superseded_by.is_some() {
            stats.superseded_card_count += 1;
        }
    }

    for ((entity, slot), cards) in single_groups {
        if cards.len() > 1 {
            let values: BTreeSet<&str> = cards.iter().map(|card| card.value.as_str()).collect();
            let record_ids = cards.iter().map(|card| card.id).collect::<Vec<_>>();

            if values.len() > 1 {
                issues.push(IntegrityIssue {
                    kind: IntegrityIssueKind::InvalidSupersession,
                    severity: IntegritySeverity::Error,
                    message: format!(
                        "single slot {}:{} has multiple active values",
                        entity, slot
                    ),
                    record_ids,
                    repair_hint: Some(
                        "supersede or retract the older active cards so only one value remains"
                            .into(),
                    ),
                });
            } else {
                issues.push(IntegrityIssue {
                    kind: IntegrityIssueKind::DuplicateActiveCard,
                    severity: IntegritySeverity::Error,
                    message: format!("duplicate active cards for {}:{}={}", entity, slot, cards[0].value),
                    record_ids,
                    repair_hint: Some(
                        "keep one active card and supersede or retract the duplicates".into(),
                    ),
                });
            }
        }
    }

    for ((entity, slot), values) in multiple_groups {
        for (value, record_ids) in values {
            if record_ids.len() > 1 {
                issues.push(IntegrityIssue {
                    kind: IntegrityIssueKind::DuplicateActiveCard,
                    severity: IntegritySeverity::Error,
                    message: format!("duplicate active cards for {}:{}={}", entity, slot, value),
                    record_ids,
                    repair_hint: Some(
                        "keep one active card and supersede or retract the duplicates".into(),
                    ),
                });
            }
        }
    }

    IntegrityReport {
        ok: issues.is_empty(),
        issues,
        stats,
    }
}

#[must_use]
pub fn core_stats(frame_store: &FrameStore, card_store: &CardStore) -> CoreStats {
    let mut stats = CoreStats::default();

    for frame in frame_store.iter() {
        stats.frame_count += 1;
        if frame.is_active() {
            stats.active_frame_count += 1;
        }
        if frame.status == FrameStatus::Superseded {
            stats.superseded_frame_count += 1;
        }
    }

    for card in card_store.iter() {
        stats.card_count += 1;
        if card.active {
            stats.active_card_count += 1;
        }
        if card.superseded_by.is_some() {
            stats.superseded_card_count += 1;
        }
    }

    stats
}

#[derive(Serialize)]
struct ActiveFrameIdentity<'a> {
    kind: FrameKind,
    content: &'a str,
    checksum: [u8; 32],
    metadata: &'a FrameMetadata,
    source: FrameSource,
    user_id: &'a str,
}

fn active_frame_identity(frame: &Frame) -> String {
    serde_json::to_string(&ActiveFrameIdentity {
        kind: frame.kind,
        content: &frame.content,
        checksum: frame.checksum,
        metadata: &frame.metadata,
        source: frame.source,
        user_id: &frame.user_id,
    })
    .expect("frame identity must serialize")
}

#[cfg(test)]
mod tests {
    use super::*;

    use crate::cards::{CardStore, MemoryCard, MemoryKind, Polarity, SchemaRegistry, VersionRelation};
    use crate::capsule::{CapsuleWriter, SectionKind};
    use crate::frame::{Frame, FrameKind, FrameSource};

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
    fn tampered_frame_checksum_mismatch_is_reported() {
        let mut store = FrameStore::new();
        let id = store.insert(Frame::new(
            0,
            FrameKind::Fact,
            "works as engineer".into(),
            "user-1".into(),
            FrameSource::Extraction,
        ));
        store.get_mut(id).unwrap().checksum = [1; 32];

        let report = scan_frame_store(&store);

        assert_eq!(report.issues.len(), 1);
        assert_eq!(report.issues[0].kind, IntegrityIssueKind::FrameChecksumMismatch);
        assert_eq!(report.issues[0].record_ids, vec![id]);
    }

    #[test]
    fn duplicate_active_cards_are_reported() {
        let schema = SchemaRegistry::new();
        let cards = vec![
            MemoryCard {
                id: 7,
                kind: MemoryKind::Fact,
                entity: "user".into(),
                slot: "likes".into(),
                value: "coffee".into(),
                polarity: Polarity::Neutral,
                version: VersionRelation::Sets,
                confidence: 1.0,
                frame_id: 0,
                created_at: 0,
                active: true,
                superseded_by: None,
            },
            MemoryCard {
                id: 8,
                kind: MemoryKind::Fact,
                entity: "user".into(),
                slot: "likes".into(),
                value: "coffee".into(),
                polarity: Polarity::Neutral,
                version: VersionRelation::Extends,
                confidence: 1.0,
                frame_id: 0,
                created_at: 0,
                active: true,
                superseded_by: None,
            },
        ];
        let bytes = serde_json::to_vec(&cards).unwrap();
        let store = CardStore::deserialize(&bytes, schema).unwrap();

        let report = scan_card_store(&store);

        assert_eq!(report.issues.len(), 1);
        assert_eq!(report.issues[0].kind, IntegrityIssueKind::DuplicateActiveCard);
        assert_eq!(report.issues[0].record_ids, vec![7, 8]);
        assert!(report.issues[0].repair_hint.is_some());
    }

    #[test]
    fn single_slot_with_multiple_active_values_is_reported() {
        let mut schema = SchemaRegistry::new();
        schema.set("user", "status", Cardinality::Single);

        let cards = vec![
            MemoryCard {
                id: 10,
                kind: MemoryKind::Fact,
                entity: "user".into(),
                slot: "status".into(),
                value: "open".into(),
                polarity: Polarity::Neutral,
                version: VersionRelation::Sets,
                confidence: 1.0,
                frame_id: 0,
                created_at: 0,
                active: true,
                superseded_by: None,
            },
            MemoryCard {
                id: 11,
                kind: MemoryKind::Fact,
                entity: "user".into(),
                slot: "status".into(),
                value: "closed".into(),
                polarity: Polarity::Neutral,
                version: VersionRelation::Extends,
                confidence: 1.0,
                frame_id: 0,
                created_at: 0,
                active: true,
                superseded_by: None,
            },
        ];
        let bytes = serde_json::to_vec(&cards).unwrap();
        let store = CardStore::deserialize(&bytes, schema).unwrap();

        let report = scan_card_store(&store);

        assert_eq!(report.issues.len(), 1);
        assert_eq!(report.issues[0].kind, IntegrityIssueKind::InvalidSupersession);
        assert_eq!(report.issues[0].record_ids, vec![10, 11]);
    }

    #[test]
    fn multiple_slot_valid_multi_value_state_is_not_flagged() {
        let mut schema = SchemaRegistry::new();
        schema.set("user", "tags", Cardinality::Multiple);

        let mut store = CardStore::new(schema);
        store.put(make_card("user", "tags", "alpha", VersionRelation::Sets));
        store.put(make_card("user", "tags", "beta", VersionRelation::Extends));

        let report = scan_card_store(&store);

        assert!(report.issues.is_empty());
    }

    #[test]
    fn core_stats_count_active_and_superseded_records_correctly() {
        let mut frame_store = FrameStore::new();
        let frame_a = frame_store.insert(Frame::new(
            0,
            FrameKind::Fact,
            "alpha".into(),
            "user-1".into(),
            FrameSource::Extraction,
        ));
        let frame_b = frame_store.insert(Frame::new(
            0,
            FrameKind::Fact,
            "beta".into(),
            "user-1".into(),
            FrameSource::Extraction,
        ));
        frame_store.get_mut(frame_a).unwrap().supersede(frame_b);

        let mut card_store = CardStore::new(SchemaRegistry::new());
        let _card_a = card_store.put(make_card("user", "employer", "Google", VersionRelation::Sets));
        let _card_b = card_store.put(make_card("user", "employer", "Meta", VersionRelation::Updates));

        let stats = core_stats(&frame_store, &card_store);

        assert_eq!(stats.frame_count, 2);
        assert_eq!(stats.active_frame_count, 1);
        assert_eq!(stats.superseded_frame_count, 1);
        assert_eq!(stats.card_count, 2);
        assert_eq!(stats.active_card_count, 1);
        assert_eq!(stats.superseded_card_count, 1);
        assert_eq!(stats.graph_node_count, 0);
        assert_eq!(stats.graph_edge_count, 0);
    }

    #[test]
    fn duplicate_active_frames_are_reported() {
        let frames = vec![
            Frame::new(
                7,
                FrameKind::Fact,
                "same fact".into(),
                "user-1".into(),
                FrameSource::Extraction,
            ),
            Frame::new(
                8,
                FrameKind::Fact,
                "same fact".into(),
                "user-1".into(),
                FrameSource::Extraction,
            ),
        ];
        let bytes = serde_json::to_vec(&frames).unwrap();
        let store = FrameStore::deserialize(&bytes).unwrap();

        let report = scan_frame_store(&store);

        assert!(report
            .issues
            .iter()
            .any(|issue| issue.kind == IntegrityIssueKind::DuplicateActiveFrame));
    }

    #[test]
    fn capsule_verification_bridge_maps_capsule_metadata_and_issues() {
        let mut writer = CapsuleWriter::new();
        writer.add_section(SectionKind::Frames, b"frame-data".to_vec());
        let mut capsule = writer.write().unwrap();
        let footer_index = capsule.len() - 1;
        capsule[footer_index] ^= 0xFF;

        let report = verify_capsule_integrity(&capsule, None);

        assert!(!report.ok);
        assert_eq!(report.stats, CoreStats::default());
        assert_eq!(report.capsule.sections.len(), 1);
        assert_eq!(report.capsule.sections[0].kind, SectionKind::Frames);
        assert_eq!(
            report.issues[0].kind,
            IntegrityIssueKind::CapsuleFooterChecksumMismatch
        );
    }
}
