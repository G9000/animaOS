//! Rules-based triplet extraction for knowledge graph ingestion.
//!
//! This complements `enrich.rs`: enrichment targets Memory Cards, while this
//! module extracts subject-predicate-object relations that map directly onto
//! animaOS's SQL-backed knowledge graph.

use std::collections::HashSet;
use std::sync::OnceLock;

use regex::Regex;
use serde::{Deserialize, Serialize};

/// A structured subject-predicate-object relation extracted from text.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Triplet {
    pub subject: String,
    pub subject_type: String,
    pub predicate: String,
    pub object: String,
    pub object_type: String,
    pub confidence: f32,
    pub matched_text: String,
    pub char_start: usize,
    pub char_end: usize,
}

impl Triplet {
    fn key(&self) -> String {
        format!(
            "{}:{}:{}",
            self.subject.to_lowercase(),
            self.predicate.to_lowercase(),
            self.object.to_lowercase()
        )
    }
}

/// Extract deterministic graph triplets from text.
#[must_use]
pub fn extract_triplets(text: &str) -> Vec<Triplet> {
    let mut triplets = Vec::new();

    extract_first_person_relations(text, &mut triplets);
    extract_family_relations(text, &mut triplets);
    extract_named_person_relations(text, &mut triplets);
    dedup_triplets(&mut triplets);

    triplets
}

fn fp_employer_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"\b(?i:(?:i\s+work\s+(?:at|for)|i(?:'m|\s+am)\s+(?:at|with)|i(?:'m|\s+am)\s+employed\s+(?:at|by)))\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,5})(?:\s*[.,;!?]|\s+(?i:as|and|where|since|for)\b|$)",
        )
        .unwrap()
    })
}

fn fp_location_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"\b(?i:(?:i\s+live\s+in|i(?:'m|\s+am)\s+(?:from|in|based\s+in)|i\s+moved\s+to))\s+([A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,4})(?:\s*[.,;!?]|\s+(?i:and|but|since|for|where)\b|$)",
        )
        .unwrap()
    })
}

fn fp_interest_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"\b(?i:(?:i\s+(?:love|like|enjoy|prefer)|i(?:'m|\s+am)\s+interested\s+in))\s+([A-Za-z][A-Za-z0-9&/'\- ]{1,40}?)(?:\s*[.,;!?]|\s+(?i:and|but|because|especially)\b|$)",
        )
        .unwrap()
    })
}

fn family_relation_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"\b(?i:my\s+(sister|brother|friend|wife|husband|spouse|coworker|colleague))\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
        )
        .unwrap()
    })
}

fn named_employer_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:works\s+(?:at|for)|is\s+(?:at|with)|is\s+employed\s+(?:at|by))\s+([A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*){0,5})(?:\s*[.,;!?]|\s+(?:as|and|where|since|for)\b|$)",
        )
        .unwrap()
    })
}

fn named_location_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:lives\s+in|is\s+from|is\s+based\s+in|moved\s+to)\s+([A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*){0,4})(?:\s*[.,;!?]|\s+(?:and|but|since|for|where)\b|$)",
        )
        .unwrap()
    })
}

fn named_married_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+is\s+married\s+to\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
        )
        .unwrap()
    })
}

fn named_interest_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:likes|loves|enjoys|prefers)\s+([A-Za-z][A-Za-z0-9&/'\- ]{1,40}?)(?:\s*[.,;!?]|\s+(?:and|but|because|especially)\b|$)",
        )
        .unwrap()
    })
}

fn extract_first_person_relations(text: &str, triplets: &mut Vec<Triplet>) {
    for caps in fp_employer_regex().captures_iter(text) {
        if let Some(triplet) = build_fixed_subject_triplet(
            &caps,
            "User",
            "person",
            "works_at",
            1,
            "organization",
            0.95,
        ) {
            triplets.push(triplet);
        }
    }

    for caps in fp_location_regex().captures_iter(text) {
        if let Some(triplet) =
            build_fixed_subject_triplet(&caps, "User", "person", "lives_in", 1, "place", 0.93)
        {
            triplets.push(triplet);
        }
    }

    for caps in fp_interest_regex().captures_iter(text) {
        if let Some(triplet) = build_fixed_subject_triplet(
            &caps,
            "User",
            "person",
            "interested_in",
            1,
            "concept",
            0.88,
        ) {
            triplets.push(triplet);
        }
    }
}

fn extract_family_relations(text: &str, triplets: &mut Vec<Triplet>) {
    for caps in family_relation_regex().captures_iter(text) {
        let Some(kind_match) = caps.get(1) else {
            continue;
        };
        let Some(predicate) = relation_for_family(kind_match.as_str()) else {
            continue;
        };
        if let Some(triplet) =
            build_fixed_subject_triplet(&caps, "User", "person", predicate, 2, "person", 0.9)
        {
            triplets.push(triplet);
        }
    }
}

fn extract_named_person_relations(text: &str, triplets: &mut Vec<Triplet>) {
    for caps in named_employer_regex().captures_iter(text) {
        if let Some(triplet) =
            build_captured_subject_triplet(&caps, 1, "person", "works_at", 2, "organization", 0.9)
        {
            triplets.push(triplet);
        }
    }

    for caps in named_location_regex().captures_iter(text) {
        if let Some(triplet) =
            build_captured_subject_triplet(&caps, 1, "person", "lives_in", 2, "place", 0.9)
        {
            triplets.push(triplet);
        }
    }

    for caps in named_married_regex().captures_iter(text) {
        if let Some(triplet) =
            build_captured_subject_triplet(&caps, 1, "person", "married_to", 2, "person", 0.92)
        {
            triplets.push(triplet);
        }
    }

    for caps in named_interest_regex().captures_iter(text) {
        if let Some(triplet) =
            build_captured_subject_triplet(&caps, 1, "person", "interested_in", 2, "concept", 0.84)
        {
            triplets.push(triplet);
        }
    }
}

fn build_fixed_subject_triplet(
    caps: &regex::Captures<'_>,
    subject: &str,
    subject_type: &str,
    predicate: &str,
    object_idx: usize,
    object_type: &str,
    confidence: f32,
) -> Option<Triplet> {
    let object = clean_capture(caps, object_idx)?;
    build_triplet(
        caps,
        subject.to_string(),
        subject_type,
        predicate,
        object,
        object_type,
        confidence,
    )
}

fn build_captured_subject_triplet(
    caps: &regex::Captures<'_>,
    subject_idx: usize,
    subject_type: &str,
    predicate: &str,
    object_idx: usize,
    object_type: &str,
    confidence: f32,
) -> Option<Triplet> {
    let subject = clean_capture(caps, subject_idx)?;
    let object = clean_capture(caps, object_idx)?;
    build_triplet(
        caps,
        subject,
        subject_type,
        predicate,
        object,
        object_type,
        confidence,
    )
}

fn build_triplet(
    caps: &regex::Captures<'_>,
    subject: String,
    subject_type: &str,
    predicate: &str,
    object: String,
    object_type: &str,
    confidence: f32,
) -> Option<Triplet> {
    let full = caps.get(0)?;
    if subject.is_empty() || object.is_empty() {
        return None;
    }

    Some(Triplet {
        subject,
        subject_type: subject_type.to_string(),
        predicate: predicate.to_string(),
        object,
        object_type: object_type.to_string(),
        confidence,
        matched_text: full.as_str().to_string(),
        char_start: full.start(),
        char_end: full.end(),
    })
}

fn clean_capture(caps: &regex::Captures<'_>, idx: usize) -> Option<String> {
    let value = caps.get(idx)?.as_str();
    let collapsed = value.split_whitespace().collect::<Vec<_>>().join(" ");
    let cleaned = collapsed
        .trim()
        .trim_matches(|c: char| matches!(c, '.' | ',' | ';' | ':' | '!' | '?'))
        .trim()
        .to_string();
    if cleaned.is_empty() {
        None
    } else {
        Some(cleaned)
    }
}

fn relation_for_family(value: &str) -> Option<&'static str> {
    match value.to_ascii_lowercase().as_str() {
        "sister" => Some("sister_of"),
        "brother" => Some("brother_of"),
        "friend" => Some("friend_of"),
        "wife" | "husband" | "spouse" => Some("married_to"),
        "coworker" | "colleague" => Some("colleague_of"),
        _ => None,
    }
}

fn dedup_triplets(triplets: &mut Vec<Triplet>) {
    let mut seen = HashSet::new();
    triplets.retain(|triplet| seen.insert(triplet.key()));
}

#[cfg(test)]
mod tests {
    use super::extract_triplets;

    #[test]
    fn extracts_first_person_employment_and_location() {
        let triplets = extract_triplets("I work at Anthropic and I live in San Francisco.");
        assert!(triplets.iter().any(|t| {
            t.subject == "User"
                && t.predicate == "works_at"
                && t.object == "Anthropic"
                && t.object_type == "organization"
        }));
        assert!(triplets.iter().any(|t| {
            t.subject == "User"
                && t.predicate == "lives_in"
                && t.object == "San Francisco"
                && t.object_type == "place"
        }));
    }

    #[test]
    fn extracts_family_and_named_person_relations() {
        let triplets =
            extract_triplets("My sister Alice works at Google and Alice lives in Munich.");
        assert!(triplets
            .iter()
            .any(|t| { t.subject == "User" && t.predicate == "sister_of" && t.object == "Alice" }));
        assert!(triplets.iter().any(|t| {
            t.subject == "Alice" && t.predicate == "works_at" && t.object == "Google"
        }));
        assert!(triplets.iter().any(|t| {
            t.subject == "Alice" && t.predicate == "lives_in" && t.object == "Munich"
        }));
    }

    #[test]
    fn deduplicates_repeated_matches() {
        let triplets = extract_triplets("I work at Anthropic. I work at Anthropic.");
        let matches = triplets
            .iter()
            .filter(|t| t.subject == "User" && t.predicate == "works_at" && t.object == "Anthropic")
            .count();
        assert_eq!(matches, 1);
    }

    #[test]
    fn returns_empty_when_no_graph_facts_are_present() {
        assert!(extract_triplets("The weather was nice today.").is_empty());
    }
}
