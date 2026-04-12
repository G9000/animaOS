//! Text normalization and cleanup utilities.
//!
//! Provides a focused pre-processing layer for ingestion and retrieval:
//! - NFKC normalization
//! - Control character stripping
//! - Whitespace compaction with newline preservation
//! - Grapheme-safe truncation
//! - Heuristic cleanup for PDF-style broken word spacing

use unicode_normalization::UnicodeNormalization;
use unicode_segmentation::UnicodeSegmentation;

/// Normalized text plus truncation metadata.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NormalizedText {
    pub text: String,
    pub truncated: bool,
}

impl NormalizedText {
    #[must_use]
    pub fn is_truncated(&self) -> bool {
        self.truncated
    }
}

/// Normalize text to NFKC, strip control chars, compact whitespace, and
/// truncate on grapheme boundaries.
///
/// `limit` is a byte limit on the returned string, but truncation will only
/// occur at grapheme cluster boundaries.
#[must_use]
pub fn normalize_text(input: &str, limit: usize) -> Option<NormalizedText> {
    let limit = limit.max(1);
    let normalized = input.nfkc().collect::<String>();

    let mut cleaned = String::with_capacity(normalized.len());
    let mut last_was_space = false;
    let mut last_was_newline = false;

    for mut ch in normalized.chars() {
        if ch == '\r' {
            ch = '\n';
        }
        if ch == '\t' {
            ch = ' ';
        }
        if ch.is_control() && ch != '\n' {
            continue;
        }

        if ch == '\n' {
            if last_was_newline {
                continue;
            }
            while cleaned.ends_with(' ') {
                cleaned.pop();
            }
            cleaned.push('\n');
            last_was_newline = true;
            last_was_space = false;
        } else if ch.is_whitespace() {
            if last_was_space || cleaned.ends_with('\n') {
                continue;
            }
            cleaned.push(' ');
            last_was_space = true;
            last_was_newline = false;
        } else {
            cleaned.push(ch);
            last_was_space = false;
            last_was_newline = false;
        }
    }

    let trimmed = cleaned.trim_matches(|c: char| c.is_whitespace());
    if trimmed.is_empty() {
        return None;
    }

    let mut truncated = false;
    let mut out = String::new();
    let mut consumed = 0usize;

    for grapheme in trimmed.graphemes(true) {
        let next = consumed + grapheme.len();
        if next > limit {
            truncated = true;
            break;
        }
        out.push_str(grapheme);
        consumed = next;
    }

    if out.is_empty() {
        if let Some(first) = trimmed.graphemes(true).next() {
            out.push_str(first);
            truncated = true;
        }
    }

    Some(NormalizedText {
        text: out,
        truncated,
    })
}

/// Return the byte index at which a string should be truncated while
/// preserving grapheme boundaries.
#[must_use]
pub fn truncate_at_grapheme_boundary(input: &str, limit: usize) -> usize {
    if input.len() <= limit {
        return input.len();
    }

    let mut end = 0usize;
    for (idx, grapheme) in input.grapheme_indices(true) {
        let next = idx + grapheme.len();
        if next > limit {
            break;
        }
        end = next;
    }

    if end == 0 {
        input.graphemes(true).next().map_or(0, str::len)
    } else {
        end
    }
}

/// Best-effort cleanup for PDF-style character fragment spacing.
///
/// Example: `man ager` -> `manager`, `C hlo e` -> `Chloe`.
#[must_use]
pub fn fix_pdf_spacing(input: &str) -> String {
    if input.len() < 3 || !input.contains(' ') {
        return input.to_string();
    }

    const VALID_SINGLE_CHARS: &[char] = &['a', 'i', 'A', 'I'];
    const COMMON_WORDS: &[&str] = &[
        "a", "an", "as", "at", "be", "by", "do", "go", "he", "if", "in", "is", "it", "me", "my",
        "no", "of", "on", "or", "so", "to", "up", "us", "we", "am", "are", "can", "did", "for",
        "get", "got", "had", "has", "her", "him", "his", "its", "let", "may", "nor", "not", "now",
        "off", "old", "one", "our", "out", "own", "ran", "run", "saw", "say", "see", "set", "she",
        "the", "too", "two", "use", "was", "way", "who", "why", "yet", "you", "all", "and", "any",
        "but", "few", "how", "man", "new", "per", "put", "via",
    ];

    fn is_common_word(s: &str) -> bool {
        let lower = s.to_ascii_lowercase();
        COMMON_WORDS.contains(&lower.as_str())
    }

    fn is_valid_single_char(s: &str) -> bool {
        s.len() == 1
            && s.chars()
                .next()
                .is_some_and(|c| VALID_SINGLE_CHARS.contains(&c))
    }

    fn is_purely_alpha(s: &str) -> bool {
        !s.is_empty() && s.chars().all(|c| c.is_alphabetic())
    }

    fn alpha_len(s: &str) -> usize {
        s.chars().filter(|c| c.is_alphabetic()).count()
    }

    fn is_orphan(word: &str) -> bool {
        alpha_len(word) == 1 && is_purely_alpha(word) && !is_valid_single_char(word)
    }

    fn is_short_fragment(word: &str) -> bool {
        let len = alpha_len(word);
        (2..=3).contains(&len) && is_purely_alpha(word) && !is_common_word(word)
    }

    fn is_likely_suffix(word: &str) -> bool {
        let len = alpha_len(word);
        len == 4 && is_purely_alpha(word) && !is_common_word(word)
    }

    fn should_start_merge(word: &str, next: &str) -> bool {
        if !is_purely_alpha(word) || !is_purely_alpha(next) {
            return false;
        }

        let word_len = alpha_len(word);
        let next_len = alpha_len(next);
        let word_common = is_common_word(word);
        let next_common = is_common_word(next);
        let word_orphan = is_orphan(word);
        let next_orphan = is_orphan(next);
        let word_fragment = is_short_fragment(word);
        let next_fragment = is_short_fragment(next);
        let next_suffix = is_likely_suffix(next);

        if word_orphan || next_orphan {
            return true;
        }
        if word_fragment && (next_fragment || next_orphan || next_suffix) {
            return true;
        }
        if is_valid_single_char(word) && next_len <= 3 && !next_common {
            return true;
        }
        if word_common && word_len <= 3 && (next_fragment || next_suffix) {
            return true;
        }

        false
    }

    fn should_continue_merge(current: &str, next: &str, had_short_fragment: bool) -> bool {
        if !had_short_fragment || !is_purely_alpha(next) {
            return false;
        }

        let next_len = alpha_len(next);
        if next_len <= 3 {
            return true;
        }
        if next_len == 4 && !is_common_word(next) && alpha_len(current) <= 5 {
            return true;
        }

        false
    }

    let words: Vec<&str> = input.split_whitespace().collect();
    if words.len() < 2 {
        return input.to_string();
    }

    let mut output: Vec<String> = Vec::with_capacity(words.len());
    let mut index = 0;

    while index < words.len() {
        let word = words[index];

        if index + 1 < words.len() && should_start_merge(word, words[index + 1]) {
            let mut merged = String::from(word);
            let mut had_short_fragment = is_short_fragment(word)
                || is_short_fragment(words[index + 1])
                || is_orphan(word)
                || is_orphan(words[index + 1])
                || (is_valid_single_char(word) && alpha_len(words[index + 1]) <= 3);

            merged.push_str(words[index + 1]);
            index += 2;

            while index < words.len()
                && should_continue_merge(&merged, words[index], had_short_fragment)
            {
                if is_short_fragment(words[index]) || is_orphan(words[index]) {
                    had_short_fragment = true;
                }
                merged.push_str(words[index]);
                index += 1;
            }

            output.push(merged);
        } else {
            output.push(word.to_string());
            index += 1;
        }
    }

    output.join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_control_and_whitespace() {
        let input = " Hello\tWorld \u{000B} test\r\nnext";
        let result = normalize_text(input, 128).expect("normalized");
        assert_eq!(result.text, "Hello World test\nnext");
        assert!(!result.truncated);
    }

    #[test]
    fn normalize_returns_none_for_empty_text() {
        assert!(normalize_text("\t  \n\r", 64).is_none());
    }

    #[test]
    fn normalize_truncates_on_grapheme_boundary() {
        let input = "a\u{0301}bcd";
        let result = normalize_text(input, 3).expect("normalized");
        assert_eq!(result.text, "\u{00E1}b");
        assert!(result.truncated);
    }

    #[test]
    fn truncate_boundary_handles_long_grapheme() {
        let input = "\u{1F1EE}\u{1F1F3}hello";
        let idx = truncate_at_grapheme_boundary(input, 4);
        assert!(idx >= 4);
        assert_eq!(&input[..idx], "\u{1F1EE}\u{1F1F3}");
    }

    #[test]
    fn fixes_pdf_spacing_single_chars() {
        assert_eq!(fix_pdf_spacing("lo n ger"), "longer");
        assert_eq!(fix_pdf_spacing("n o"), "no");
    }

    #[test]
    fn fixes_pdf_spacing_preserves_normal_text() {
        assert_eq!(
            fix_pdf_spacing("The manager reported to the supervisor"),
            "The manager reported to the supervisor"
        );
        assert_eq!(fix_pdf_spacing("man ager"), "manager");
        assert_eq!(fix_pdf_spacing("I am a person"), "I am a person");
    }

    #[test]
    fn fixes_pdf_spacing_real_artifacts() {
        assert_eq!(fix_pdf_spacing("C hlo e"), "Chloe");
        assert_eq!(fix_pdf_spacing("C hlo e Nguyen"), "Chloe Nguyen");
    }

    #[test]
    fn fixes_pdf_spacing_two_letter_fragments() {
        assert_eq!(fix_pdf_spacing("lo ng"), "long");
        assert_eq!(fix_pdf_spacing("to be or"), "to be or");
    }
}
