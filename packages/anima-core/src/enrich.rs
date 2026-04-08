//! Rules-based memory extraction engine.
//!
//! Fast-path extraction via regex patterns *before* any LLM call.
//! Produces structured extractions compatible with [`crate::cards::MemoryCard`].
//!
//! Adapted from memvid's `RulesEngine` (~40 patterns) and animaOS's
//! `PatternExtractor` (~10 patterns). This module targets 30+ patterns
//! covering both first-person and third-person speech.

use std::sync::OnceLock;

use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::cards::MemoryKind;

// ── Extraction Output ────────────────────────────────────────────────

/// A single structured extraction from text.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Extraction {
    /// Which rule produced this extraction.
    pub rule_name: String,
    /// The entity this fact is about (e.g. "user", "Alice").
    pub entity: String,
    /// The slot/attribute (e.g. "employer", "age", "food_preference").
    pub slot: String,
    /// The extracted value.
    pub value: String,
    /// What kind of memory this is.
    pub kind: MemoryKind,
    /// Confidence: 1.0 for exact regex match, lower for fuzzy.
    pub confidence: f32,
    /// The matched span in the source text.
    pub matched_text: String,
    /// Character offset of the match start in the source.
    pub char_start: usize,
    /// Character offset of the match end (exclusive).
    pub char_end: usize,
}

// ── Rule Definition ──────────────────────────────────────────────────

/// A single extraction rule: regex pattern → structured output.
#[derive(Debug, Clone)]
pub struct ExtractionRule {
    /// Human-readable name (e.g. "first_person_employer").
    pub name: String,
    /// Compiled regex pattern.
    pattern: Regex,
    /// Memory kind for extractions from this rule.
    pub kind: MemoryKind,
    /// Template for the entity field. `$0` = full match, `$1`-`$9` = capture groups.
    pub entity_template: String,
    /// Template for the slot field.
    pub slot_template: String,
    /// Template for the value field.
    pub value_template: String,
    /// Base confidence for matches.
    pub confidence: f32,
}

impl ExtractionRule {
    /// Create a new rule. Returns `None` if the regex is invalid.
    #[must_use]
    pub fn new(
        name: impl Into<String>,
        pattern: &str,
        kind: MemoryKind,
        entity_template: impl Into<String>,
        slot_template: impl Into<String>,
        value_template: impl Into<String>,
    ) -> Option<Self> {
        let re = Regex::new(pattern).ok()?;
        Some(Self {
            name: name.into(),
            pattern: re,
            kind,
            entity_template: entity_template.into(),
            slot_template: slot_template.into(),
            value_template: value_template.into(),
            confidence: 1.0,
        })
    }

    /// Create a rule with custom confidence.
    #[must_use]
    pub fn with_confidence(mut self, confidence: f32) -> Self {
        self.confidence = confidence;
        self
    }

    /// Apply this rule to text, returning all matches.
    pub fn extract(&self, text: &str) -> Vec<Extraction> {
        self.pattern
            .captures_iter(text)
            .map(|caps| {
                let full = caps.get(0).unwrap();
                Extraction {
                    rule_name: self.name.clone(),
                    entity: expand_template(&self.entity_template, &caps),
                    slot: expand_template(&self.slot_template, &caps),
                    value: expand_template(&self.value_template, &caps),
                    kind: self.kind,
                    confidence: self.confidence,
                    matched_text: full.as_str().to_string(),
                    char_start: full.start(),
                    char_end: full.end(),
                }
            })
            .collect()
    }
}

/// Expand a template string replacing `$0`, `$1`, ..., `$9` with capture groups.
fn expand_template(template: &str, caps: &regex::Captures<'_>) -> String {
    let mut result = template.to_string();
    // Replace $0 .. $9, highest first to avoid $1 matching in $10
    for i in (0..=9).rev() {
        let placeholder = format!("${i}");
        if let Some(m) = caps.get(i) {
            result = result.replace(&placeholder, m.as_str().trim());
        } else {
            result = result.replace(&placeholder, "");
        }
    }
    result
}

// ── Rules Engine ─────────────────────────────────────────────────────

/// Collection of extraction rules applied in order.
#[derive(Debug, Clone)]
pub struct RulesEngine {
    rules: Vec<ExtractionRule>,
    /// Deduplicate extractions with same (entity, slot, value).
    pub dedup: bool,
}

impl Default for RulesEngine {
    fn default() -> Self {
        Self {
            rules: default_rules(),
            dedup: true,
        }
    }
}

impl RulesEngine {
    /// Create an empty engine (no default rules).
    #[must_use]
    pub fn empty() -> Self {
        Self {
            rules: Vec::new(),
            dedup: true,
        }
    }

    /// Create engine with default rules.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Add a rule.
    pub fn add_rule(&mut self, rule: ExtractionRule) {
        self.rules.push(rule);
    }

    /// Number of rules.
    #[must_use]
    pub fn rule_count(&self) -> usize {
        self.rules.len()
    }

    /// Run all rules against text, returning extractions.
    pub fn extract(&self, text: &str) -> Vec<Extraction> {
        let mut results: Vec<Extraction> = Vec::new();

        for rule in &self.rules {
            results.extend(rule.extract(text));
        }

        // Always run structural extractors
        results.extend(extract_emails(text));
        results.extend(extract_urls(text));
        results.extend(extract_dates(text));

        if self.dedup {
            dedup_extractions(&mut results);
        }

        results
    }

    /// Run rules and return only extractions above a confidence threshold.
    pub fn extract_above(&self, text: &str, min_confidence: f32) -> Vec<Extraction> {
        self.extract(text)
            .into_iter()
            .filter(|e| e.confidence >= min_confidence)
            .collect()
    }
}

// ── Structural Extractors ────────────────────────────────────────────

fn email_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\b([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})\b").unwrap())
}

fn url_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r#"\bhttps?://[^\s<>")\]]+"#).unwrap())
}

fn date_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(
            r"(?i)\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s*\d{4})\b",
        )
        .unwrap()
    })
}

fn extract_emails(text: &str) -> Vec<Extraction> {
    email_regex()
        .find_iter(text)
        .map(|m| Extraction {
            rule_name: "email".to_string(),
            entity: "user".to_string(),
            slot: "email".to_string(),
            value: m.as_str().to_string(),
            kind: MemoryKind::Profile,
            confidence: 1.0,
            matched_text: m.as_str().to_string(),
            char_start: m.start(),
            char_end: m.end(),
        })
        .collect()
}

fn extract_urls(text: &str) -> Vec<Extraction> {
    url_regex()
        .find_iter(text)
        .map(|m| Extraction {
            rule_name: "url".to_string(),
            entity: "user".to_string(),
            slot: "url".to_string(),
            value: m.as_str().to_string(),
            kind: MemoryKind::Fact,
            confidence: 0.8,
            matched_text: m.as_str().to_string(),
            char_start: m.start(),
            char_end: m.end(),
        })
        .collect()
}

fn extract_dates(text: &str) -> Vec<Extraction> {
    date_regex()
        .find_iter(text)
        .map(|m| Extraction {
            rule_name: "date".to_string(),
            entity: "user".to_string(),
            slot: "date".to_string(),
            value: m.as_str().to_string(),
            kind: MemoryKind::Event,
            confidence: 0.7,
            matched_text: m.as_str().to_string(),
            char_start: m.start(),
            char_end: m.end(),
        })
        .collect()
}

// ── Deduplication ────────────────────────────────────────────────────

fn dedup_extractions(extractions: &mut Vec<Extraction>) {
    let mut seen = std::collections::HashSet::new();
    extractions.retain(|e| {
        let key = format!("{}:{}:{}", e.entity, e.slot, e.value.to_lowercase());
        seen.insert(key)
    });
}

// ── Default Rules ────────────────────────────────────────────────────

/// Build the default set of extraction rules.
///
/// Covers first-person ("I work at ...", "my name is ...") and
/// third-person ("she works at ...", "his name is ...") patterns,
/// adapted from both memvid's RulesEngine and animaOS's PatternExtractor.
fn default_rules() -> Vec<ExtractionRule> {
    let rules_spec: Vec<(&str, &str, MemoryKind, &str, &str, &str)> = vec![
        // ── First-person: Employment ─────────────────────────
        (
            "fp_employer",
            r"(?i)\b(?:i\s+work\s+(?:at|for)|i'm\s+(?:at|with)|i\s+am\s+(?:at|with)|i'm\s+employed\s+(?:at|by))\s+([A-Z][\w\s&.']+?)(?:\s*[.,;!?]|\s+(?:as|and|doing|where|since|for)|\s*$)",
            MemoryKind::Profile,
            "user",
            "employer",
            "$1",
        ),
        (
            "fp_job_title",
            r"(?i)\b(?:i\s+am\s+a|i'm\s+a|i\s+work\s+as\s+(?:a|an)?|my\s+(?:job|role|title|position)\s+is)\s+([a-zA-Z][\w\s-]{2,30}?)(?:\s*[.,;!?]|\s+(?:at|for|in|and|with)|\s*$)",
            MemoryKind::Profile,
            "user",
            "job_title",
            "$1",
        ),
        // ── First-person: Location ───────────────────────────
        (
            "fp_location",
            r"(?i)\b(?:i\s+live\s+in|i'm\s+(?:from|in|based\s+in)|i\s+am\s+(?:from|in|based\s+in)|i\s+moved\s+to)\s+([A-Z][\w\s,]+?)(?:\s*[.!?;]|\s+(?:and|but|since|for|where)|\s*$)",
            MemoryKind::Profile,
            "user",
            "location",
            "$1",
        ),
        // ── First-person: Name ───────────────────────────────
        (
            "fp_name",
            r"(?i)\b(?:my\s+name\s+is|i'm|i\s+am|call\s+me|they\s+call\s+me)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
            MemoryKind::Profile,
            "user",
            "name",
            "$1",
        ),
        // ── First-person: Age ────────────────────────────────
        (
            "fp_age",
            r"(?i)\b(?:i\s+am|i'm)\s+(\d{1,3})\s+(?:years?\s+old|yo)\b",
            MemoryKind::Profile,
            "user",
            "age",
            "$1",
        ),
        // ── First-person: Birthday ───────────────────────────
        (
            "fp_birthday",
            r"(?i)\b(?:my\s+birthday\s+is|i\s+was\s+born\s+(?:on|in))\s+(.+?)(?:\s*[.!?;]|\s*$)",
            MemoryKind::Event,
            "user",
            "birthday",
            "$1",
        ),
        // ── First-person: Preferences ────────────────────────
        (
            "fp_food_like",
            r"(?i)\b(?:i\s+(?:love|like|enjoy|prefer)\s+(?:eating\s+)?|my\s+fav(?:orite|ourite)\s+food\s+is\s+)([a-zA-Z][\w\s]+?)(?:\s*[.!?;,]|\s+(?:and|but|because|especially)|\s*$)",
            MemoryKind::Preference,
            "user",
            "food_preference",
            "$1",
        ),
        (
            "fp_food_dislike",
            r"(?i)\b(?:i\s+(?:hate|dislike|can't\s+stand|don't\s+like)\s+(?:eating\s+)?)([a-zA-Z][\w\s]+?)(?:\s*[.!?;,]|\s+(?:and|but|because|especially)|\s*$)",
            MemoryKind::Preference,
            "user",
            "food_dislike",
            "$1",
        ),
        (
            "fp_allergy",
            r"(?i)\b(?:i(?:'m|\s+am)\s+allergic\s+to|i\s+have\s+(?:a\s+)?(?:allergy|allergies)\s+to)\s+([a-zA-Z][\w\s]+?)(?:\s*[.!?;,]|\s*$)",
            MemoryKind::Profile,
            "user",
            "allergy",
            "$1",
        ),
        // ── First-person: Hobbies ────────────────────────────
        (
            "fp_hobby",
            r"(?i)\b(?:i\s+(?:love|like|enjoy)\s+(?:to\s+)?(?:go\s+)?|my\s+hobb(?:y|ies)\s+(?:is|are|include)\s+)([a-zA-Z][\w\s]+?)(?:\s*[.!?;,]|\s+(?:and|but|because|when|after)|\s*$)",
            MemoryKind::Preference,
            "user",
            "hobby",
            "$1",
        ),
        // ── First-person: Pets ───────────────────────────────
        (
            "fp_pet",
            r"(?i)\b(?:i\s+have\s+(?:a|an)\s+)(\w+)\s+(?:named|called)\s+(\w+)\b",
            MemoryKind::Fact,
            "user",
            "pet_$1",
            "$2",
        ),
        // ── First-person: Family ─────────────────────────────
        (
            "fp_family",
            r"(?i)\bmy\s+(wife|husband|partner|daughter|son|mother|father|mom|dad|sister|brother|child|kid)\s+(?:is\s+(?:named\s+)?|(?:'s\s+name\s+is\s+))([A-Z][a-z]+)\b",
            MemoryKind::Relationship,
            "user",
            "$1",
            "$2",
        ),
        (
            "fp_family_named",
            r"(?i)\bmy\s+(wife|husband|partner|daughter|son|mother|father|mom|dad|sister|brother)\s+([A-Z][a-z]+)\b",
            MemoryKind::Relationship,
            "user",
            "$1",
            "$2",
        ),
        // ── First-person: Education ──────────────────────────
        (
            "fp_education",
            r"(?i)\b(?:i\s+(?:studied|went\s+to|graduated\s+from|attend(?:ed)?)|my\s+(?:school|university|college)\s+is)\s+([A-Z][\w\s&.']+?)(?:\s*[.!?;,]|\s+(?:and|but|where|in|for)|\s*$)",
            MemoryKind::Profile,
            "user",
            "education",
            "$1",
        ),
        // ── First-person: Travel ─────────────────────────────
        (
            "fp_travel",
            r"(?i)\b(?:i(?:'ve|\s+have)\s+(?:been\s+to|visited)|i\s+(?:went|traveled|travelled)\s+to)\s+([A-Z][\w\s,]+?)(?:\s*[.!?;,]|\s*$)",
            MemoryKind::Event,
            "user",
            "travel",
            "$1",
        ),
        // ── First-person: Language ───────────────────────────
        (
            "fp_language",
            r"(?i)\b(?:i\s+speak|i\s+(?:am\s+)?fluent\s+in|i\s+know)\s+([A-Z][a-z]+(?:(?:\s+and\s+|\s*,\s*)[A-Z][a-z]+)*)\b",
            MemoryKind::Profile,
            "user",
            "language",
            "$1",
        ),
        // ── Third-person: Employment ─────────────────────────
        (
            "tp_employer",
            r"(?i)\b([A-Z][a-z]+)\s+works?\s+(?:at|for)\s+([A-Z][\w\s&.']+?)(?:\s*[.,;!?]|\s+(?:as|and|doing)|\s*$)",
            MemoryKind::Profile,
            "$1",
            "employer",
            "$2",
        ),
        (
            "tp_job_title",
            r"(?i)\b([A-Z][a-z]+)\s+is\s+(?:a|an)\s+([a-zA-Z][\w\s-]{2,30}?)(?:\s*[.,;!?]|\s+(?:at|for|in|and|with)|\s*$)",
            MemoryKind::Profile,
            "$1",
            "job_title",
            "$2",
        ),
        // ── Third-person: Location ───────────────────────────
        (
            "tp_location",
            r"(?i)\b([A-Z][a-z]+)\s+(?:lives?\s+in|is\s+(?:from|based\s+in)|moved\s+to)\s+([A-Z][\w\s,]+?)(?:\s*[.!?;]|\s*$)",
            MemoryKind::Profile,
            "$1",
            "location",
            "$2",
        ),
        // ── Third-person: Age ────────────────────────────────
        (
            "tp_age",
            r"(?i)\b([A-Z][a-z]+)\s+is\s+(\d{1,3})\s+(?:years?\s+old|yo)\b",
            MemoryKind::Profile,
            "$1",
            "age",
            "$2",
        ),
        // ── Third-person: Preferences ────────────────────────
        (
            "tp_preference",
            r"(?i)\b([A-Z][a-z]+)\s+(?:loves?|likes?|enjoys?|prefers?)\s+([a-zA-Z][\w\s]+?)(?:\s*[.!?;,]|\s+(?:and|but|because)|\s*$)",
            MemoryKind::Preference,
            "$1",
            "preference",
            "$2",
        ),
        // ── Third-person: Relationship ───────────────────────
        (
            "tp_relationship",
            r"(?i)\b([A-Z][a-z]+)\s+(?:is\s+(?:married\s+to|dating|engaged\s+to)|'s\s+(?:wife|husband|partner)\s+is)\s+([A-Z][a-z]+)\b",
            MemoryKind::Relationship,
            "$1",
            "partner",
            "$2",
        ),
        // ── Third-person: Education ──────────────────────────
        (
            "tp_education",
            r"(?i)\b([A-Z][a-z]+)\s+(?:studied|went\s+to|graduated\s+from|attends?)\s+([A-Z][\w\s&.']+?)(?:\s*[.!?;,]|\s*$)",
            MemoryKind::Profile,
            "$1",
            "education",
            "$2",
        ),
        // ── Third-person: Pet ────────────────────────────────
        (
            "tp_pet",
            r"(?i)\b([A-Z][a-z]+)(?:'s)?\s+(?:has\s+(?:a|an)\s+)?(\w+)\s+(?:named|called)\s+(\w+)\b",
            MemoryKind::Fact,
            "$1",
            "pet_$2",
            "$3",
        ),
        // ── Third-person: Family ─────────────────────────────
        (
            "tp_family",
            r"(?i)\b([A-Z][a-z]+)(?:'s)?\s+(wife|husband|partner|daughter|son|mother|father|sister|brother)\s+(?:is\s+(?:named\s+)?)?([A-Z][a-z]+)\b",
            MemoryKind::Relationship,
            "$1",
            "$2",
            "$3",
        ),
    ];

    rules_spec
        .into_iter()
        .filter_map(|(name, pattern, kind, entity, slot, value)| {
            ExtractionRule::new(name, pattern, kind, entity, slot, value)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn engine() -> RulesEngine {
        RulesEngine::new()
    }

    #[test]
    fn test_default_rules_created() {
        let e = engine();
        assert!(
            e.rule_count() >= 25,
            "should have 25+ default rules, got {}",
            e.rule_count()
        );
    }

    #[test]
    fn test_fp_employer() {
        let e = engine();
        let results = e.extract("I work at Google as a software engineer.");
        let emp: Vec<_> = results.iter().filter(|r| r.slot == "employer").collect();
        assert!(!emp.is_empty(), "should extract employer");
        assert_eq!(emp[0].value, "Google");
        assert_eq!(emp[0].entity, "user");
    }

    #[test]
    fn test_fp_job_title() {
        let e = engine();
        let results = e.extract("I'm a senior software engineer at Meta.");
        let jobs: Vec<_> = results.iter().filter(|r| r.slot == "job_title").collect();
        assert!(!jobs.is_empty(), "should extract job title");
        assert!(jobs[0]
            .value
            .to_lowercase()
            .contains("senior software engineer"));
    }

    #[test]
    fn test_fp_location() {
        let e = engine();
        let results = e.extract("I live in San Francisco.");
        let locs: Vec<_> = results.iter().filter(|r| r.slot == "location").collect();
        assert!(!locs.is_empty(), "should extract location");
        assert!(locs[0].value.contains("San Francisco"));
    }

    #[test]
    fn test_fp_name() {
        let e = engine();
        let results = e.extract("My name is Alice Chen.");
        let names: Vec<_> = results.iter().filter(|r| r.slot == "name").collect();
        assert!(!names.is_empty(), "should extract name");
        assert!(names[0].value.contains("Alice"));
    }

    #[test]
    fn test_fp_age() {
        let e = engine();
        let results = e.extract("I'm 32 years old.");
        let ages: Vec<_> = results.iter().filter(|r| r.slot == "age").collect();
        assert!(!ages.is_empty(), "should extract age");
        assert_eq!(ages[0].value, "32");
    }

    #[test]
    fn test_fp_birthday() {
        let e = engine();
        let results = e.extract("My birthday is March 15.");
        let bdays: Vec<_> = results.iter().filter(|r| r.slot == "birthday").collect();
        assert!(!bdays.is_empty(), "should extract birthday");
        assert!(bdays[0].value.contains("March 15"));
    }

    #[test]
    fn test_fp_allergy() {
        let e = engine();
        let results = e.extract("I'm allergic to peanuts.");
        let allergies: Vec<_> = results.iter().filter(|r| r.slot == "allergy").collect();
        assert!(!allergies.is_empty(), "should extract allergy");
        assert!(allergies[0].value.to_lowercase().contains("peanut"));
    }

    #[test]
    fn test_fp_pet() {
        let e = engine();
        let results = e.extract("I have a cat named Mittens.");
        let pets: Vec<_> = results
            .iter()
            .filter(|r| r.slot.starts_with("pet"))
            .collect();
        assert!(!pets.is_empty(), "should extract pet");
        assert!(pets[0].value.contains("Mittens"));
    }

    #[test]
    fn test_fp_family() {
        let e = engine();
        let results = e.extract("My wife is named Sarah.");
        let fam: Vec<_> = results.iter().filter(|r| r.slot == "wife").collect();
        assert!(!fam.is_empty(), "should extract family relation");
        assert!(fam[0].value.contains("Sarah"));
    }

    #[test]
    fn test_tp_employer() {
        let e = engine();
        let results = e.extract("Alice works at Microsoft.");
        let emp: Vec<_> = results
            .iter()
            .filter(|r| r.slot == "employer" && r.entity == "Alice")
            .collect();
        assert!(!emp.is_empty(), "should extract third-person employer");
        assert!(emp[0].value.contains("Microsoft"));
    }

    #[test]
    fn test_tp_location() {
        let e = engine();
        let results = e.extract("Bob lives in Tokyo.");
        let locs: Vec<_> = results
            .iter()
            .filter(|r| r.slot == "location" && r.entity == "Bob")
            .collect();
        assert!(!locs.is_empty(), "should extract third-person location");
        assert!(locs[0].value.contains("Tokyo"));
    }

    #[test]
    fn test_tp_age() {
        let e = engine();
        let results = e.extract("Carlos is 45 years old.");
        let ages: Vec<_> = results
            .iter()
            .filter(|r| r.slot == "age" && r.entity == "Carlos")
            .collect();
        assert!(!ages.is_empty(), "should extract third-person age");
        assert_eq!(ages[0].value, "45");
    }

    #[test]
    fn test_email_extraction() {
        let e = engine();
        let results = e.extract("Reach me at alice@example.com please.");
        let emails: Vec<_> = results.iter().filter(|r| r.slot == "email").collect();
        assert!(!emails.is_empty(), "should extract email");
        assert_eq!(emails[0].value, "alice@example.com");
    }

    #[test]
    fn test_url_extraction() {
        let e = engine();
        let results = e.extract("Check out https://github.com/myproject for details.");
        let urls: Vec<_> = results.iter().filter(|r| r.slot == "url").collect();
        assert!(!urls.is_empty(), "should extract URL");
        assert!(urls[0].value.contains("github.com"));
    }

    #[test]
    fn test_date_extraction() {
        let e = engine();
        let results = e.extract("The meeting is on 2024-03-15 at noon.");
        let dates: Vec<_> = results.iter().filter(|r| r.slot == "date").collect();
        assert!(!dates.is_empty(), "should extract date");
        assert!(dates[0].value.contains("2024-03-15"));
    }

    #[test]
    fn test_multiple_extractions() {
        let e = engine();
        let text =
            "I'm Alice, I work at Google and I live in Seattle. My email is alice@google.com.";
        let results = e.extract(text);
        assert!(
            results.len() >= 3,
            "should extract multiple facts, got {}",
            results.len()
        );
    }

    #[test]
    fn test_dedup() {
        let e = engine();
        // Same fact expressed twice
        let text = "I live in Paris. Did I mention I live in Paris?";
        let results = e.extract(text);
        let locs: Vec<_> = results.iter().filter(|r| r.slot == "location").collect();
        assert_eq!(locs.len(), 1, "should dedup identical extractions");
    }

    #[test]
    fn test_empty_input() {
        let e = engine();
        let results = e.extract("");
        assert!(results.is_empty());
    }

    #[test]
    fn test_no_match() {
        let e = engine();
        let results = e.extract("The quick brown fox jumps over the lazy dog.");
        // May extract some low-confidence matches, but shouldn't have high-confidence profile data
        let profile: Vec<_> = results
            .iter()
            .filter(|r| r.kind == MemoryKind::Profile && r.confidence >= 1.0)
            .collect();
        assert!(
            profile.is_empty(),
            "should not extract profile from unrelated text"
        );
    }

    #[test]
    fn test_custom_rule() {
        let mut e = RulesEngine::empty();
        let rule = ExtractionRule::new(
            "custom_test",
            r"(?i)favorite color is (\w+)",
            MemoryKind::Preference,
            "user",
            "favorite_color",
            "$1",
        )
        .unwrap();
        e.add_rule(rule);
        let results = e.extract("My favorite color is blue.");
        let colors: Vec<_> = results
            .iter()
            .filter(|r| r.slot == "favorite_color")
            .collect();
        assert!(!colors.is_empty());
        assert_eq!(colors[0].value, "blue");
    }

    #[test]
    fn test_confidence_filter() {
        let e = engine();
        let results = e.extract("Visit https://example.com and I live in Berlin.");
        let high = e.extract_above("Visit https://example.com and I live in Berlin.", 0.9);
        assert!(high.len() <= results.len());
        for h in &high {
            assert!(h.confidence >= 0.9);
        }
    }

    #[test]
    fn test_extraction_offsets() {
        let e = engine();
        let text = "I live in Tokyo.";
        let results = e.extract(text);
        let locs: Vec<_> = results.iter().filter(|r| r.slot == "location").collect();
        if !locs.is_empty() {
            assert!(locs[0].char_start < locs[0].char_end);
            assert!(locs[0].char_end <= text.len());
        }
    }

    #[test]
    fn test_fp_education() {
        let e = engine();
        let results = e.extract("I graduated from MIT.");
        let edu: Vec<_> = results.iter().filter(|r| r.slot == "education").collect();
        assert!(!edu.is_empty(), "should extract education");
        assert!(edu[0].value.contains("MIT"));
    }

    #[test]
    fn test_fp_travel() {
        let e = engine();
        let results = e.extract("I've been to Japan.");
        let travel: Vec<_> = results.iter().filter(|r| r.slot == "travel").collect();
        assert!(!travel.is_empty(), "should extract travel");
        assert!(travel[0].value.contains("Japan"));
    }

    #[test]
    fn test_tp_relationship() {
        let e = engine();
        let results = e.extract("Alice is married to Bob.");
        let rels: Vec<_> = results.iter().filter(|r| r.slot == "partner").collect();
        assert!(!rels.is_empty(), "should extract relationship");
    }
}
