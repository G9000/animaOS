//! Temporal mention parsing and timeline assembly.
//!
//! Converts natural-language time references ("last Tuesday", "2 days ago")
//! into UTC timestamps for frame filtering and timeline queries.

use chrono::{DateTime, NaiveDateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::frame::{Frame, FrameId};

/// A parsed temporal mention with resolved timestamp.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TemporalMention {
    /// Original text that was parsed.
    pub raw: String,
    /// Resolved UTC timestamp.
    pub timestamp: i64,
    /// Confidence in the parse (0.0–1.0).
    pub confidence: f32,
}

/// Parse a temporal expression relative to `now`.
///
/// Supports:
/// - Relative: "2 days ago", "last week", "yesterday", "3 hours ago"
/// - ISO 8601: "2024-01-15T10:30:00Z"
/// - Date-only: "2024-01-15"
///
/// Uses `chrono-english` for natural language parsing, with fallbacks
/// for common patterns.
pub fn parse_temporal(input: &str, now: DateTime<Utc>) -> Option<TemporalMention> {
    let trimmed = input.trim();
    if trimmed.is_empty() {
        return None;
    }

    // Try chrono-english first
    if let Ok(dt) = chrono_english::parse_date_string(trimmed, now, chrono_english::Dialect::Us) {
        return Some(TemporalMention {
            raw: trimmed.to_string(),
            timestamp: dt.timestamp(),
            confidence: 0.9,
        });
    }

    // Try ISO 8601
    if let Ok(dt) = DateTime::parse_from_rfc3339(trimmed) {
        return Some(TemporalMention {
            raw: trimmed.to_string(),
            timestamp: dt.timestamp(),
            confidence: 1.0,
        });
    }

    // Try date-only (YYYY-MM-DD)
    if let Ok(nd) = chrono::NaiveDate::parse_from_str(trimmed, "%Y-%m-%d") {
        let dt = nd
            .and_hms_opt(0, 0, 0)
            .map(|ndt| DateTime::<Utc>::from_naive_utc_and_offset(ndt, Utc));
        if let Some(dt) = dt {
            return Some(TemporalMention {
                raw: trimmed.to_string(),
                timestamp: dt.timestamp(),
                confidence: 0.95,
            });
        }
    }

    // Try "N unit(s) ago" pattern
    if let Some(ts) = parse_relative_ago(trimmed, now) {
        return Some(ts);
    }

    None
}

/// Parse "N unit(s) ago" patterns.
fn parse_relative_ago(input: &str, now: DateTime<Utc>) -> Option<TemporalMention> {
    let lower = input.to_lowercase();
    let parts: Vec<&str> = lower.split_whitespace().collect();

    if parts.len() < 3 || parts.last() != Some(&"ago") {
        return None;
    }

    let n: i64 = parts[0].parse().ok()?;
    if n < 0 || n > 365 * 100 {
        return None; // Reject negative and absurdly large values
    }

    let unit = parts[1].trim_end_matches('s');
    let seconds = match unit {
        "second" | "sec" => n,
        "minute" | "min" => n * 60,
        "hour" | "hr" => n * 3600,
        "day" => n * 86400,
        "week" | "wk" => n * 7 * 86400,
        "month" | "mo" => n * 30 * 86400,
        "year" | "yr" => n * 365 * 86400,
        _ => return None,
    };

    let ts = now.timestamp() - seconds;
    Some(TemporalMention {
        raw: input.to_string(),
        timestamp: ts,
        confidence: 0.85,
    })
}

/// Timeline query builder for filtering frames by time range.
#[derive(Debug, Clone)]
pub struct TimelineQuery {
    /// Start of time range (inclusive).
    pub start: Option<i64>,
    /// End of time range (inclusive).
    pub end: Option<i64>,
    /// Maximum number of results.
    pub limit: Option<usize>,
}

impl TimelineQuery {
    pub fn new() -> Self {
        Self {
            start: None,
            end: None,
            limit: None,
        }
    }

    /// Set start from a temporal expression.
    pub fn since(mut self, input: &str, now: DateTime<Utc>) -> Self {
        if let Some(mention) = parse_temporal(input, now) {
            self.start = Some(mention.timestamp);
        }
        self
    }

    /// Set end from a temporal expression.
    pub fn until(mut self, input: &str, now: DateTime<Utc>) -> Self {
        if let Some(mention) = parse_temporal(input, now) {
            self.end = Some(mention.timestamp);
        }
        self
    }

    /// Set start from a unix timestamp.
    pub fn start_ts(mut self, ts: i64) -> Self {
        self.start = Some(ts);
        self
    }

    /// Set end from a unix timestamp.
    pub fn end_ts(mut self, ts: i64) -> Self {
        self.end = Some(ts);
        self
    }

    pub fn limit(mut self, n: usize) -> Self {
        self.limit = Some(n);
        self
    }

    /// Filter and sort frames by this query.
    pub fn apply<'a>(&self, frames: &'a [Frame]) -> Vec<&'a Frame> {
        let mut results: Vec<&Frame> = frames
            .iter()
            .filter(|f| {
                if let Some(start) = self.start {
                    if f.timestamp < start {
                        return false;
                    }
                }
                if let Some(end) = self.end {
                    if f.timestamp > end {
                        return false;
                    }
                }
                true
            })
            .collect();

        // Sort by timestamp descending (newest first)
        results.sort_by(|a, b| b.timestamp.cmp(&a.timestamp));

        if let Some(limit) = self.limit {
            results.truncate(limit);
        }

        results
    }
}

impl Default for TimelineQuery {
    fn default() -> Self {
        Self::new()
    }
}

/// Assemble a timeline from frames, grouping by day.
///
/// Returns: Vec<(date_str, frames)> sorted newest first.
pub fn assemble_timeline(frames: &[Frame]) -> Vec<(String, Vec<&Frame>)> {
    use std::collections::BTreeMap;

    let mut groups: BTreeMap<String, Vec<&Frame>> = BTreeMap::new();

    for frame in frames {
        let dt = DateTime::from_timestamp(frame.timestamp, 0)
            .unwrap_or_else(|| DateTime::from_timestamp(0, 0).unwrap());
        let date_str = dt.format("%Y-%m-%d").to_string();
        groups.entry(date_str).or_default().push(frame);
    }

    // Sort each group by timestamp descending
    for group in groups.values_mut() {
        group.sort_by(|a, b| b.timestamp.cmp(&a.timestamp));
    }

    // Return in reverse chronological order
    let mut result: Vec<(String, Vec<&Frame>)> = groups.into_iter().collect();
    result.reverse();
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn fixed_now() -> DateTime<Utc> {
        Utc.with_ymd_and_hms(2024, 6, 15, 12, 0, 0).unwrap()
    }

    #[test]
    fn test_parse_iso8601() {
        let result = parse_temporal("2024-01-15T10:30:00Z", fixed_now()).unwrap();
        assert_eq!(result.confidence, 1.0);
        assert!(result.timestamp > 0);
    }

    #[test]
    fn test_parse_date_only() {
        let result = parse_temporal("2024-01-15", fixed_now()).unwrap();
        assert_eq!(result.confidence, 0.95);
    }

    #[test]
    fn test_parse_relative_ago() {
        let now = fixed_now();
        let result = parse_temporal("2 days ago", now).unwrap();
        let expected = now.timestamp() - 2 * 86400;
        assert!((result.timestamp - expected).abs() <= 1);
        assert_eq!(result.confidence, 0.85);
    }

    #[test]
    fn test_parse_hours_ago() {
        let now = fixed_now();
        let result = parse_temporal("3 hours ago", now).unwrap();
        let expected = now.timestamp() - 3 * 3600;
        assert!((result.timestamp - expected).abs() <= 1);
    }

    #[test]
    fn test_parse_yesterday() {
        let result = parse_temporal("yesterday", fixed_now());
        // chrono-english should handle "yesterday"
        assert!(result.is_some());
    }

    #[test]
    fn test_parse_empty() {
        assert!(parse_temporal("", fixed_now()).is_none());
    }

    #[test]
    fn test_parse_nonsense() {
        assert!(parse_temporal("blarg florp", fixed_now()).is_none());
    }

    #[test]
    fn test_timeline_query() {
        use crate::frame::{Frame, FrameKind};

        let frames: Vec<Frame> = (0..5)
            .map(|i| {
                let mut f = Frame::new(FrameKind::Fact, format!("fact {i}"), "user1".into());
                f.timestamp = 1000 + i * 100;
                f
            })
            .collect();

        let query = TimelineQuery::new().start_ts(1100).end_ts(1300).limit(10);

        let results = query.apply(&frames);
        assert_eq!(results.len(), 3); // timestamps 1100, 1200, 1300
                                      // Should be newest first
        assert!(results[0].timestamp >= results[1].timestamp);
    }

    #[test]
    fn test_assemble_timeline() {
        use crate::frame::{Frame, FrameKind};

        // Two frames on different days
        let mut f1 = Frame::new(FrameKind::Fact, "day1".into(), "user1".into());
        f1.timestamp = 1718400000; // 2024-06-15 00:00:00 UTC approximately
        let mut f2 = Frame::new(FrameKind::Fact, "day2".into(), "user1".into());
        f2.timestamp = 1718400000 + 86400;

        let timeline = assemble_timeline(&[f1, f2]);
        assert_eq!(timeline.len(), 2);
        // Newest day first
        assert!(timeline[0].0 > timeline[1].0);
    }
}
