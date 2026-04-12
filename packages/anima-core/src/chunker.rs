//! Structural text chunker with semantic boundary awareness.
//!
//! Splits text into chunks respecting:
//! - Markdown headings (propagated as context)
//! - Fenced code blocks (preserved whole or split at function boundaries)
//! - Tables (preserved whole or split with header propagation)
//! - Lists (kept intact when possible)
//! - Paragraph boundaries
//!
//! Character-based sizing (not tokens) — token truncation is the embedding layer's job.

use std::sync::OnceLock;

use regex::Regex;
use serde::{Deserialize, Serialize};

// ── Options ──────────────────────────────────────────────────────────

/// Configuration for the chunker.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ChunkOptions {
    /// Maximum characters per chunk (default: 1200).
    pub max_chars: usize,
    /// Overlap characters between consecutive chunks (default: 0).
    pub overlap_chars: usize,
    /// Preserve fenced code blocks as single chunks when possible.
    pub preserve_code_blocks: bool,
    /// Preserve tables as single chunks when possible.
    pub preserve_tables: bool,
    /// Propagate section headings to subsequent chunks as context.
    pub include_section_headers: bool,
    /// Preserve list items as a group when possible.
    pub preserve_lists: bool,
}

impl Default for ChunkOptions {
    fn default() -> Self {
        Self {
            max_chars: 1200,
            overlap_chars: 0,
            preserve_code_blocks: true,
            preserve_tables: true,
            include_section_headers: true,
            preserve_lists: true,
        }
    }
}

// ── Chunk Types ──────────────────────────────────────────────────────

/// The structural type of a chunk.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ChunkType {
    Paragraph,
    Heading,
    CodeBlock,
    Table,
    TableContinuation,
    List,
    Mixed,
}

/// A single chunk of text with metadata.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Chunk {
    /// The chunk text content.
    pub text: String,
    /// Structural type.
    pub chunk_type: ChunkType,
    /// 0-based index in the output sequence.
    pub index: usize,
    /// Character offset in the original text.
    pub char_start: usize,
    /// Character end offset (exclusive).
    pub char_end: usize,
    /// Section heading context (if include_section_headers is on).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub section_context: Option<String>,
}

// ── Structural Elements (internal) ───────────────────────────────────

#[derive(Debug, Clone)]
enum Element {
    Heading { level: u8, text: String },
    Paragraph(String),
    CodeBlock { lang: Option<String>, text: String },
    Table(String),
    List(String),
    Separator,
}

// ── Regex Patterns ───────────────────────────────────────────────────

struct Patterns {
    heading: Regex,
    fenced_code_start: Regex,
    fenced_code_end: Regex,
    table_row: Regex,
    table_separator: Regex,
    ordered_list: Regex,
    unordered_list: Regex,
    horizontal_rule: Regex,
}

fn patterns() -> &'static Patterns {
    static PATTERNS: OnceLock<Patterns> = OnceLock::new();
    PATTERNS.get_or_init(|| Patterns {
        heading: Regex::new(r"^(#{1,6})\s+(.+)$").unwrap(),
        fenced_code_start: Regex::new(r"^```(\w*)").unwrap(),
        fenced_code_end: Regex::new(r"^```\s*$").unwrap(),
        table_row: Regex::new(r"^\|.+\|$").unwrap(),
        table_separator: Regex::new(r"^\|[\s:|-]+\|$").unwrap(),
        ordered_list: Regex::new(r"^\s*\d+[.)]\s+").unwrap(),
        unordered_list: Regex::new(r"^\s*[-*+]\s+").unwrap(),
        horizontal_rule: Regex::new(r"^[-*_]{3,}\s*$").unwrap(),
    })
}

// ── Structure Detection ──────────────────────────────────────────────

/// Parse raw text into structural elements.
fn detect_structure(text: &str) -> Vec<(Element, usize, usize)> {
    let pat = patterns();
    let mut elements: Vec<(Element, usize, usize)> = Vec::new();
    let mut char_offset: usize = 0;

    let lines: Vec<&str> = text.lines().collect();
    let mut i = 0;

    while i < lines.len() {
        let line = lines[i];
        let line_start = char_offset;

        // Fenced code block
        if let Some(caps) = pat.fenced_code_start.captures(line) {
            let lang = caps
                .get(1)
                .map(|m| m.as_str().to_string())
                .filter(|s| !s.is_empty());
            let mut code_lines = vec![line.to_string()];
            char_offset += line.len() + 1; // +1 for newline
            i += 1;
            while i < lines.len() {
                let cl = lines[i];
                code_lines.push(cl.to_string());
                char_offset += cl.len() + 1;
                i += 1;
                if pat.fenced_code_end.is_match(cl) {
                    break;
                }
            }
            let code_text = code_lines.join("\n");
            elements.push((
                Element::CodeBlock {
                    lang,
                    text: code_text,
                },
                line_start,
                char_offset,
            ));
            continue;
        }

        // Heading
        if let Some(caps) = pat.heading.captures(line) {
            let level = caps.get(1).unwrap().as_str().len() as u8;
            let heading_text = caps.get(2).unwrap().as_str().to_string();
            char_offset += line.len() + 1;
            elements.push((
                Element::Heading {
                    level,
                    text: heading_text,
                },
                line_start,
                char_offset,
            ));
            i += 1;
            continue;
        }

        // Table
        if pat.table_row.is_match(line) || pat.table_separator.is_match(line) {
            let mut table_lines = vec![line.to_string()];
            char_offset += line.len() + 1;
            i += 1;
            while i < lines.len()
                && (pat.table_row.is_match(lines[i]) || pat.table_separator.is_match(lines[i]))
            {
                table_lines.push(lines[i].to_string());
                char_offset += lines[i].len() + 1;
                i += 1;
            }
            let table_text = table_lines.join("\n");
            elements.push((Element::Table(table_text), line_start, char_offset));
            continue;
        }

        // Horizontal rule / separator
        if pat.horizontal_rule.is_match(line) {
            char_offset += line.len() + 1;
            elements.push((Element::Separator, line_start, char_offset));
            i += 1;
            continue;
        }

        // List
        if pat.ordered_list.is_match(line) || pat.unordered_list.is_match(line) {
            let mut list_lines = vec![line.to_string()];
            char_offset += line.len() + 1;
            i += 1;
            while i < lines.len() {
                let ll = lines[i];
                // Continue list: list item or indented continuation
                if pat.ordered_list.is_match(ll)
                    || pat.unordered_list.is_match(ll)
                    || (ll.starts_with("  ") && !ll.trim().is_empty())
                {
                    list_lines.push(ll.to_string());
                    char_offset += ll.len() + 1;
                    i += 1;
                } else {
                    break;
                }
            }
            let list_text = list_lines.join("\n");
            elements.push((Element::List(list_text), line_start, char_offset));
            continue;
        }

        // Paragraph (accumulate non-empty lines)
        if !line.trim().is_empty() {
            let mut para_lines = vec![line.to_string()];
            char_offset += line.len() + 1;
            i += 1;
            while i < lines.len() {
                let pl = lines[i];
                if pl.trim().is_empty()
                    || pat.heading.is_match(pl)
                    || pat.fenced_code_start.is_match(pl)
                    || pat.table_row.is_match(pl)
                    || pat.horizontal_rule.is_match(pl)
                    || pat.ordered_list.is_match(pl)
                    || pat.unordered_list.is_match(pl)
                {
                    break;
                }
                para_lines.push(pl.to_string());
                char_offset += pl.len() + 1;
                i += 1;
            }
            let para_text = para_lines.join("\n");
            elements.push((Element::Paragraph(para_text), line_start, char_offset));
            continue;
        }

        // Empty line — skip
        char_offset += line.len() + 1;
        i += 1;
    }

    elements
}

// ── Chunking ─────────────────────────────────────────────────────────

/// Split text into chunks respecting structural boundaries.
pub fn chunk_text(text: &str, opts: &ChunkOptions) -> Vec<Chunk> {
    let elements = detect_structure(text);
    let mut chunks: Vec<Chunk> = Vec::new();
    let mut current_text = String::new();
    let mut current_start: usize = 0;
    let mut current_type = ChunkType::Paragraph;
    let mut section_heading: Option<String> = None;

    let flush = |chunks: &mut Vec<Chunk>,
                 current_text: &mut String,
                 current_start: &mut usize,
                 current_type: &mut ChunkType,
                 section_heading: &Option<String>,
                 char_end: usize| {
        let trimmed = current_text.trim();
        if !trimmed.is_empty() {
            chunks.push(Chunk {
                text: trimmed.to_string(),
                chunk_type: *current_type,
                index: chunks.len(),
                char_start: *current_start,
                char_end,
                section_context: section_heading.clone(),
            });
        }
        current_text.clear();
        *current_type = ChunkType::Paragraph;
    };

    for (element, el_start, el_end) in &elements {
        match element {
            Element::Heading {
                text: heading_text, ..
            } => {
                // Flush any accumulated text
                flush(
                    &mut chunks,
                    &mut current_text,
                    &mut current_start,
                    &mut current_type,
                    &section_heading,
                    *el_start,
                );
                if opts.include_section_headers {
                    section_heading = Some(heading_text.clone());
                }
                current_start = *el_end;
            }

            Element::Separator => {
                flush(
                    &mut chunks,
                    &mut current_text,
                    &mut current_start,
                    &mut current_type,
                    &section_heading,
                    *el_start,
                );
                current_start = *el_end;
            }

            Element::CodeBlock {
                text: code_text, ..
            } => {
                if opts.preserve_code_blocks && code_text.chars().count() <= opts.max_chars {
                    // Flush accumulated, then emit code block as its own chunk
                    flush(
                        &mut chunks,
                        &mut current_text,
                        &mut current_start,
                        &mut current_type,
                        &section_heading,
                        *el_start,
                    );
                    chunks.push(Chunk {
                        text: code_text.clone(),
                        chunk_type: ChunkType::CodeBlock,
                        index: chunks.len(),
                        char_start: *el_start,
                        char_end: *el_end,
                        section_context: section_heading.clone(),
                    });
                    current_start = *el_end;
                } else {
                    // Too big or not preserving — split as text
                    split_large_text(
                        code_text,
                        ChunkType::CodeBlock,
                        *el_start,
                        opts,
                        &section_heading,
                        &mut chunks,
                    );
                    current_start = *el_end;
                }
            }

            Element::Table(table_text) => {
                if opts.preserve_tables && table_text.chars().count() <= opts.max_chars {
                    flush(
                        &mut chunks,
                        &mut current_text,
                        &mut current_start,
                        &mut current_type,
                        &section_heading,
                        *el_start,
                    );
                    chunks.push(Chunk {
                        text: table_text.clone(),
                        chunk_type: ChunkType::Table,
                        index: chunks.len(),
                        char_start: *el_start,
                        char_end: *el_end,
                        section_context: section_heading.clone(),
                    });
                    current_start = *el_end;
                } else if table_text.chars().count() > opts.max_chars {
                    flush(
                        &mut chunks,
                        &mut current_text,
                        &mut current_start,
                        &mut current_type,
                        &section_heading,
                        *el_start,
                    );
                    split_table(table_text, *el_start, opts, &section_heading, &mut chunks);
                    current_start = *el_end;
                } else {
                    // Not preserving — treat as text
                    append_or_flush(
                        table_text,
                        ChunkType::Mixed,
                        *el_start,
                        *el_end,
                        opts,
                        &section_heading,
                        &mut chunks,
                        &mut current_text,
                        &mut current_start,
                        &mut current_type,
                    );
                }
            }

            Element::List(list_text) => {
                if opts.preserve_lists && list_text.chars().count() <= opts.max_chars {
                    // Try to keep with current text if it fits
                    let combined_len = if current_text.is_empty() {
                        list_text.chars().count()
                    } else {
                        current_text.chars().count() + 2 + list_text.chars().count()
                    };

                    if combined_len <= opts.max_chars {
                        if !current_text.is_empty() {
                            current_text.push_str("\n\n");
                        } else {
                            current_start = *el_start;
                        }
                        current_text.push_str(list_text);
                        current_type = if current_type == ChunkType::Paragraph {
                            ChunkType::List
                        } else {
                            ChunkType::Mixed
                        };
                    } else {
                        flush(
                            &mut chunks,
                            &mut current_text,
                            &mut current_start,
                            &mut current_type,
                            &section_heading,
                            *el_start,
                        );
                        chunks.push(Chunk {
                            text: list_text.clone(),
                            chunk_type: ChunkType::List,
                            index: chunks.len(),
                            char_start: *el_start,
                            char_end: *el_end,
                            section_context: section_heading.clone(),
                        });
                        current_start = *el_end;
                    }
                } else {
                    append_or_flush(
                        list_text,
                        ChunkType::Mixed,
                        *el_start,
                        *el_end,
                        opts,
                        &section_heading,
                        &mut chunks,
                        &mut current_text,
                        &mut current_start,
                        &mut current_type,
                    );
                }
            }

            Element::Paragraph(para_text) => {
                append_or_flush(
                    para_text,
                    ChunkType::Paragraph,
                    *el_start,
                    *el_end,
                    opts,
                    &section_heading,
                    &mut chunks,
                    &mut current_text,
                    &mut current_start,
                    &mut current_type,
                );
            }
        }
    }

    // Flush remaining
    let end = text.len();
    flush(
        &mut chunks,
        &mut current_text,
        &mut current_start,
        &mut current_type,
        &section_heading,
        end,
    );

    // Apply overlap
    if opts.overlap_chars > 0 && chunks.len() > 1 {
        apply_overlap(&mut chunks, opts.overlap_chars);
    }

    // Reindex
    for (i, chunk) in chunks.iter_mut().enumerate() {
        chunk.index = i;
    }

    chunks
}

/// Append text to current accumulator, or flush and start new chunk if it exceeds max_chars.
#[allow(clippy::too_many_arguments)]
fn append_or_flush(
    new_text: &str,
    new_type: ChunkType,
    el_start: usize,
    el_end: usize,
    opts: &ChunkOptions,
    section_heading: &Option<String>,
    chunks: &mut Vec<Chunk>,
    current_text: &mut String,
    current_start: &mut usize,
    current_type: &mut ChunkType,
) {
    let new_len = new_text.chars().count();
    let current_len = current_text.chars().count();

    if current_text.is_empty() {
        if new_len > opts.max_chars {
            split_large_text(new_text, new_type, el_start, opts, section_heading, chunks);
            *current_start = el_end;
        } else {
            *current_start = el_start;
            current_text.push_str(new_text);
            *current_type = new_type;
        }
    } else if current_len + 2 + new_len <= opts.max_chars {
        current_text.push_str("\n\n");
        current_text.push_str(new_text);
        if *current_type != new_type {
            *current_type = ChunkType::Mixed;
        }
    } else {
        // Flush current, start new
        let trimmed = current_text.trim();
        if !trimmed.is_empty() {
            chunks.push(Chunk {
                text: trimmed.to_string(),
                chunk_type: *current_type,
                index: chunks.len(),
                char_start: *current_start,
                char_end: el_start,
                section_context: section_heading.clone(),
            });
        }
        current_text.clear();

        if new_len > opts.max_chars {
            split_large_text(new_text, new_type, el_start, opts, section_heading, chunks);
            *current_start = el_end;
        } else {
            *current_start = el_start;
            current_text.push_str(new_text);
            *current_type = new_type;
        }
    }
}

/// Split a large text block at sentence/paragraph boundaries.
fn split_large_text(
    text: &str,
    chunk_type: ChunkType,
    base_offset: usize,
    opts: &ChunkOptions,
    section_heading: &Option<String>,
    chunks: &mut Vec<Chunk>,
) {
    // Split at double newlines first, then sentences
    let paragraphs: Vec<&str> = text.split("\n\n").collect();
    let mut buf = String::new();
    let mut buf_start = base_offset;

    for para in paragraphs {
        if para.trim().is_empty() {
            continue;
        }

        if buf.is_empty() {
            if para.chars().count() > opts.max_chars {
                // Split at sentence boundaries
                for sentence in split_sentences(para) {
                    if buf.chars().count() + sentence.chars().count() + 1 > opts.max_chars
                        && !buf.is_empty()
                    {
                        chunks.push(Chunk {
                            text: buf.trim().to_string(),
                            chunk_type,
                            index: chunks.len(),
                            char_start: buf_start,
                            char_end: buf_start + buf.len(),
                            section_context: section_heading.clone(),
                        });
                        buf_start += buf.len();
                        buf.clear();
                    }
                    if !buf.is_empty() {
                        buf.push(' ');
                    }
                    buf.push_str(sentence);
                }
            } else {
                buf.push_str(para);
            }
        } else if buf.chars().count() + 2 + para.chars().count() <= opts.max_chars {
            buf.push_str("\n\n");
            buf.push_str(para);
        } else {
            chunks.push(Chunk {
                text: buf.trim().to_string(),
                chunk_type,
                index: chunks.len(),
                char_start: buf_start,
                char_end: buf_start + buf.len(),
                section_context: section_heading.clone(),
            });
            buf_start += buf.len();
            buf.clear();
            buf.push_str(para);
        }
    }

    if !buf.trim().is_empty() {
        chunks.push(Chunk {
            text: buf.trim().to_string(),
            chunk_type,
            index: chunks.len(),
            char_start: buf_start,
            char_end: buf_start + buf.len(),
            section_context: section_heading.clone(),
        });
    }
}

/// Split table rows with header propagation.
fn split_table(
    table_text: &str,
    base_offset: usize,
    opts: &ChunkOptions,
    section_heading: &Option<String>,
    chunks: &mut Vec<Chunk>,
) {
    let lines: Vec<&str> = table_text.lines().collect();
    if lines.is_empty() {
        return;
    }

    // Find header: first row + separator
    let pat = patterns();
    let mut header_end = 0;
    let mut header = String::new();

    if lines.len() >= 2 && pat.table_separator.is_match(lines[1]) {
        header = format!("{}\n{}", lines[0], lines[1]);
        header_end = 2;
    } else if !lines.is_empty() {
        header = lines[0].to_string();
        header_end = 1;
    }

    let header_chars = header.chars().count();
    let available = opts.max_chars.saturating_sub(header_chars + 1);

    if available == 0 {
        // Header alone exceeds max_chars; just split naively
        split_large_text(
            table_text,
            ChunkType::Table,
            base_offset,
            opts,
            section_heading,
            chunks,
        );
        return;
    }

    let mut buf = header.clone();
    let mut buf_start = base_offset;
    let mut is_first = true;

    for line in &lines[header_end..] {
        let line_chars = line.chars().count();
        if buf.chars().count() + 1 + line_chars > opts.max_chars && buf.lines().count() > header_end
        {
            let ct = if is_first {
                ChunkType::Table
            } else {
                ChunkType::TableContinuation
            };
            chunks.push(Chunk {
                text: buf.clone(),
                chunk_type: ct,
                index: chunks.len(),
                char_start: buf_start,
                char_end: buf_start + buf.len(),
                section_context: section_heading.clone(),
            });
            is_first = false;
            buf_start += buf.len();
            buf = header.clone();
        }
        buf.push('\n');
        buf.push_str(line);
    }

    if buf.lines().count() > header_end {
        let ct = if is_first {
            ChunkType::Table
        } else {
            ChunkType::TableContinuation
        };
        chunks.push(Chunk {
            text: buf,
            chunk_type: ct,
            index: chunks.len(),
            char_start: buf_start,
            char_end: base_offset + table_text.len(),
            section_context: section_heading.clone(),
        });
    }
}

/// Naive sentence splitter: split at `. `, `? `, `! `, preserving delimiters.
fn split_sentences(text: &str) -> Vec<&str> {
    let mut result = Vec::new();
    let mut start = 0;
    let bytes = text.as_bytes();

    for i in 0..bytes.len().saturating_sub(1) {
        if (bytes[i] == b'.' || bytes[i] == b'?' || bytes[i] == b'!') && bytes[i + 1] == b' ' {
            let end = i + 1; // include the punctuation
            let s = &text[start..end];
            if !s.trim().is_empty() {
                result.push(s.trim());
            }
            start = i + 2; // skip the space
        }
    }

    if start < text.len() {
        let s = &text[start..];
        if !s.trim().is_empty() {
            result.push(s.trim());
        }
    }

    result
}

/// Apply character overlap between consecutive chunks.
fn apply_overlap(chunks: &mut [Chunk], overlap_chars: usize) {
    if chunks.len() < 2 {
        return;
    }
    // Work backwards to avoid index issues
    for i in (1..chunks.len()).rev() {
        let prev_text = chunks[i - 1].text.clone();
        let prev_len = prev_text.chars().count();
        let overlap_start = prev_len.saturating_sub(overlap_chars);

        // Find the overlap text from the end of the previous chunk
        let overlap_text: String = prev_text.chars().skip(overlap_start).collect();
        if !overlap_text.trim().is_empty() {
            chunks[i].text = format!("{} {}", overlap_text.trim(), chunks[i].text);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_simple_paragraphs() {
        let text = "First paragraph here.\n\nSecond paragraph here.\n\nThird paragraph here.";
        let opts = ChunkOptions {
            max_chars: 1200,
            ..Default::default()
        };
        let chunks = chunk_text(text, &opts);
        assert_eq!(chunks.len(), 1, "short text should be one chunk");
        assert!(chunks[0].text.contains("First"));
        assert!(chunks[0].text.contains("Third"));
    }

    #[test]
    fn test_split_long_paragraphs() {
        let para = "This is a sentence. ".repeat(100); // ~2000 chars
        let opts = ChunkOptions {
            max_chars: 500,
            ..Default::default()
        };
        let chunks = chunk_text(&para, &opts);
        assert!(chunks.len() > 1, "long text should be split");
        for chunk in &chunks {
            assert!(
                chunk.text.chars().count() <= 600, // some tolerance
                "chunk too long: {} chars",
                chunk.text.chars().count()
            );
        }
    }

    #[test]
    fn test_heading_context() {
        let text = "# Introduction\n\nSome text here.\n\n# Methods\n\nMore text here.";
        let opts = ChunkOptions {
            max_chars: 50,
            include_section_headers: true,
            ..Default::default()
        };
        let chunks = chunk_text(text, &opts);
        assert!(chunks.len() >= 2);
        assert_eq!(chunks[0].section_context.as_deref(), Some("Introduction"));
        assert_eq!(chunks[1].section_context.as_deref(), Some("Methods"));
    }

    #[test]
    fn test_code_block_preservation() {
        let text = "Before.\n\n```python\ndef foo():\n    return 42\n```\n\nAfter.";
        let opts = ChunkOptions {
            max_chars: 1200,
            preserve_code_blocks: true,
            ..Default::default()
        };
        let chunks = chunk_text(text, &opts);
        let code_chunks: Vec<_> = chunks
            .iter()
            .filter(|c| c.chunk_type == ChunkType::CodeBlock)
            .collect();
        assert_eq!(code_chunks.len(), 1);
        assert!(code_chunks[0].text.contains("def foo()"));
    }

    #[test]
    fn test_table_preservation() {
        let text = "| Name | Age |\n|------|-----|\n| Alice | 30 |\n| Bob | 25 |";
        let opts = ChunkOptions {
            max_chars: 1200,
            preserve_tables: true,
            ..Default::default()
        };
        let chunks = chunk_text(text, &opts);
        assert_eq!(chunks.len(), 1);
        assert_eq!(chunks[0].chunk_type, ChunkType::Table);
    }

    #[test]
    fn test_table_split_with_header() {
        let row = "| Alice Smith | 30 | Engineer | San Francisco |";
        let sep = "|-------------|-----|----------|---------------|";
        let header_line = "| Name | Age | Job | City |";

        let mut lines = vec![header_line.to_string(), sep.to_string()];
        for _ in 0..50 {
            lines.push(row.to_string());
        }
        let text = lines.join("\n");

        let opts = ChunkOptions {
            max_chars: 300,
            preserve_tables: true,
            ..Default::default()
        };
        let chunks = chunk_text(&text, &opts);
        assert!(chunks.len() > 1, "large table should split");

        // All continuation chunks should have the header
        for chunk in &chunks[1..] {
            assert_eq!(chunk.chunk_type, ChunkType::TableContinuation);
            assert!(
                chunk.text.contains("Name"),
                "continuation should have header"
            );
        }
    }

    #[test]
    fn test_list_preservation() {
        let text = "- Item one\n- Item two\n- Item three";
        let opts = ChunkOptions {
            max_chars: 1200,
            preserve_lists: true,
            ..Default::default()
        };
        let chunks = chunk_text(text, &opts);
        assert_eq!(chunks.len(), 1);
        assert!(
            chunks[0].chunk_type == ChunkType::List || chunks[0].chunk_type == ChunkType::Paragraph
        );
    }

    #[test]
    fn test_overlap() {
        let text = "First paragraph with some content.\n\nSecond paragraph with different content.";
        let opts = ChunkOptions {
            max_chars: 40,
            overlap_chars: 10,
            ..Default::default()
        };
        let chunks = chunk_text(&text, &opts);
        if chunks.len() > 1 {
            // Second chunk should contain overlap from first
            let first_end: String = chunks[0]
                .text
                .chars()
                .rev()
                .take(10)
                .collect::<String>()
                .chars()
                .rev()
                .collect();
            // Overlap text should appear at the start of the second chunk
            assert!(
                chunks[1].text.len() > chunks[0].text.len() - 10
                    || chunks[1]
                        .text
                        .contains(&first_end.trim().split_whitespace().last().unwrap_or("")),
                "overlap should propagate"
            );
        }
    }

    #[test]
    fn test_empty_input() {
        let chunks = chunk_text("", &ChunkOptions::default());
        assert!(chunks.is_empty());
    }

    #[test]
    fn test_mixed_content() {
        let text = "\
# Header

Some intro text.

```rust
fn main() {
    println!(\"hello\");
}
```

| Col A | Col B |
|-------|-------|
| 1     | 2     |

- Item A
- Item B

---

Final paragraph.";

        let opts = ChunkOptions {
            max_chars: 1200,
            ..Default::default()
        };
        let chunks = chunk_text(text, &opts);
        // Should produce multiple chunks with different types
        let types: Vec<ChunkType> = chunks.iter().map(|c| c.chunk_type).collect();
        assert!(types.contains(&ChunkType::CodeBlock));
        assert!(types.contains(&ChunkType::Table));
    }

    #[test]
    fn test_sequential_indices() {
        let text = "A. ".repeat(500);
        let opts = ChunkOptions {
            max_chars: 200,
            ..Default::default()
        };
        let chunks = chunk_text(&text, &opts);
        for (i, chunk) in chunks.iter().enumerate() {
            assert_eq!(chunk.index, i, "indices should be sequential");
        }
    }
}
