// SPDX-License-Identifier: Apache-2.0
// Bounded result-shaping pattern adapted from OpenAI Codex codex-rs/file-system/src/lib.rs at
// 9e552e9d15ba52bed7077d5357f3e18e330f8f38; grep implementation is ANIMA-specific.

use std::io::{BufRead, BufReader};

use regex::Regex;

use crate::{
    walk_page, BackendPath, ContentClassification, EntryKind, FileToolError, OperationControl,
    SearchableBackend, ValidatedLimits, WalkCursor, WalkEntry, WalkOptions, WalkPage,
    MAX_PATTERN_BYTES,
};

// Reserves enough response budget for backend-enriched identity metadata such
// as CoreFS stable IDs, revisions, content hashes, and catalog generation.
const RESPONSE_ITEM_OVERHEAD_BYTES: usize = 256;
const RESPONSE_SKIP_OVERHEAD_BYTES: usize = 192;
const RESPONSE_PAGE_OVERHEAD_BYTES: usize = 64;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum GrepMode {
    Literal,
    Regex,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GrepCursor {
    path: String,
    byte_offset: Option<u64>,
    walk_after: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GrepRequest {
    pub root: BackendPath,
    pub query: String,
    pub mode: GrepMode,
    pub cursor: Option<GrepCursor>,
    pub max_files: usize,
    pub max_matches: usize,
    pub max_line_bytes: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GrepMatch {
    pub path: BackendPath,
    pub line_number: usize,
    pub byte_offset: u64,
    pub excerpt: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SkipReason {
    BinaryContent,
    InvalidUtf8,
    LineTooLong,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GrepSkipped {
    pub path: BackendPath,
    pub reason: SkipReason,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct GrepPage {
    pub matches: Vec<GrepMatch>,
    pub skipped: Vec<GrepSkipped>,
    pub next_cursor: Option<GrepCursor>,
    pub truncated: bool,
    pub limit_reached: bool,
}

enum Matcher {
    Literal(String),
    Regex(Regex),
}

impl Matcher {
    fn new(query: &str, mode: GrepMode) -> Result<Self, FileToolError> {
        if query.is_empty() {
            return Err(FileToolError::InvalidPattern {
                mode: match mode {
                    GrepMode::Literal => "literal",
                    GrepMode::Regex => "regex",
                },
                message: "pattern must not be empty".to_string(),
            });
        }
        match mode {
            GrepMode::Literal => Ok(Self::Literal(query.to_string())),
            GrepMode::Regex => {
                Regex::new(query)
                    .map(Self::Regex)
                    .map_err(|error| FileToolError::InvalidPattern {
                        mode: "regex",
                        message: error.to_string(),
                    })
            }
        }
    }

    fn offsets<'a>(&'a self, line: &'a str) -> Box<dyn Iterator<Item = usize> + 'a> {
        match self {
            Self::Literal(query) => Box::new(line.match_indices(query).map(|(offset, _)| offset)),
            Self::Regex(regex) => Box::new(regex.find_iter(line).map(|found| found.start())),
        }
    }
}

pub fn grep(
    backend: &dyn SearchableBackend,
    request: GrepRequest,
    limits: ValidatedLimits,
    control: OperationControl,
) -> Result<GrepPage, FileToolError> {
    control.check()?;
    let selected_backend = backend.capabilities().backend();
    if request.root.backend() != selected_backend {
        return Err(FileToolError::BackendMismatch {
            path_backend: request.root.backend(),
            selected_backend,
        });
    }
    validate_request(&request, limits)?;
    let matcher = Matcher::new(&request.query, request.mode)?;
    let walk_cursor = request
        .cursor
        .as_ref()
        .and_then(GrepCursor::resume_walk_cursor);
    let initial_walk_after = walk_cursor
        .as_ref()
        .map(|cursor| cursor.as_str().to_owned());
    let root_metadata = backend.metadata(request.root.as_str())?;
    let walk = if root_metadata.kind == EntryKind::File {
        WalkPage {
            entries: vec![WalkEntry {
                path: request.root.clone(),
                kind: root_metadata.kind,
                is_symlink: root_metadata.is_symlink,
                size: root_metadata.size,
                content: root_metadata.content,
                depth: 0,
            }],
            ..WalkPage::default()
        }
    } else {
        walk_page(
            backend,
            request.root.clone(),
            WalkOptions {
                page_size: request.max_files,
                cursor: walk_cursor,
                include_directories: false,
            },
            limits,
            control.clone(),
        )
        .map_err(|error| match error {
            FileToolError::PaginationCannotAdvance { .. } => {
                FileToolError::PaginationCannotAdvance { operation: "grep" }
            }
            error => error,
        })?
    };
    let walk_truncated = walk.truncated;
    let walk_limit_reached = walk.limit_reached;
    let walk_next_cursor = walk.next_cursor;
    let mut page = GrepPage::default();
    let mut response_bytes = RESPONSE_PAGE_OVERHEAD_BYTES;
    let mut output_truncated = false;
    let mut last_progress = None;
    let mut traversal_after = initial_walk_after;
    let offset_cursor = request
        .cursor
        .as_ref()
        .filter(|cursor| cursor.byte_offset.is_some());
    let mut offset_cursor_reached = offset_cursor.is_none();

    'files: for entry in walk.entries {
        control.check()?;
        let entry_path = entry.path.as_str().to_owned();
        let entry_walk_after = traversal_after.clone();
        if entry.kind != EntryKind::File {
            traversal_after = Some(entry_path);
            continue;
        }
        if !offset_cursor_reached {
            if offset_cursor.is_some_and(|cursor| cursor.path == entry.path.as_str()) {
                offset_cursor_reached = true;
            } else {
                traversal_after = Some(entry_path);
                continue;
            }
        }
        let content = if entry.content == ContentClassification::Unknown {
            backend.metadata(entry.path.as_str())?.content
        } else {
            entry.content
        };
        if content == ContentClassification::Binary {
            let skipped = GrepSkipped {
                path: entry.path,
                reason: SkipReason::BinaryContent,
            };
            if !push_skip(&mut page, &mut response_bytes, skipped, limits)? {
                output_truncated = true;
                break 'files;
            }
            last_progress = Some(GrepCursor::after_file(entry_path.clone()));
            traversal_after = Some(entry_path);
            continue;
        }
        let mut reader = BufReader::new(backend.open_read(entry.path.as_str())?);
        let mut line_number = 0usize;
        let mut file_offset = 0u64;
        let mut file_matches = Vec::new();
        let mut file_skips = Vec::<GrepSkipped>::new();
        let mut file_response_bytes = 0usize;
        let mut invalid_file = None;
        let mut file_truncated = false;
        loop {
            control.check()?;
            let Some(line) = read_bounded_line(&mut reader, request.max_line_bytes, &control)?
            else {
                break;
            };
            line_number += 1;
            let line_start = file_offset;
            file_offset = file_offset.saturating_add(line.consumed as u64);
            if line.too_long {
                if !file_skips
                    .iter()
                    .any(|skipped| skipped.reason == SkipReason::LineTooLong)
                {
                    file_skips.push(GrepSkipped {
                        path: entry.path.clone(),
                        reason: SkipReason::LineTooLong,
                    });
                }
                break;
            }
            if line.bytes.contains(&0) {
                invalid_file = Some(SkipReason::BinaryContent);
                break;
            }
            let text = match std::str::from_utf8(&line.bytes) {
                Ok(text) => text.trim_end_matches(['\r', '\n']),
                Err(_) => {
                    invalid_file = Some(SkipReason::InvalidUtf8);
                    break;
                }
            };
            if !file_truncated {
                for match_offset in matcher.offsets(text) {
                    let byte_offset = line_start.saturating_add(match_offset as u64);
                    if !is_after_cursor(request.cursor.as_ref(), entry.path.as_str(), byte_offset) {
                        continue;
                    }
                    let item_bytes = entry
                        .path
                        .as_str()
                        .len()
                        .saturating_mul(12)
                        .saturating_add(text.len().saturating_mul(6))
                        .saturating_add(RESPONSE_ITEM_OVERHEAD_BYTES);
                    let Some(next_file_response_bytes) =
                        file_response_bytes.checked_add(item_bytes)
                    else {
                        if page.matches.is_empty()
                            && page.skipped.is_empty()
                            && file_matches.is_empty()
                        {
                            return Err(FileToolError::ResponseItemTooLarge {
                                kind: "grep_match",
                                required: usize::MAX,
                                maximum: limits.response_bytes(),
                            });
                        }
                        file_truncated = true;
                        break;
                    };
                    let Some(next_response_bytes) =
                        response_bytes.checked_add(next_file_response_bytes)
                    else {
                        if page.matches.is_empty()
                            && page.skipped.is_empty()
                            && file_matches.is_empty()
                        {
                            return Err(FileToolError::ResponseItemTooLarge {
                                kind: "grep_match",
                                required: usize::MAX,
                                maximum: limits.response_bytes(),
                            });
                        }
                        file_truncated = true;
                        break;
                    };
                    if next_response_bytes > limits.response_bytes()
                        || page.matches.len() + file_matches.len() == request.max_matches
                    {
                        if page.matches.is_empty()
                            && page.skipped.is_empty()
                            && file_matches.is_empty()
                        {
                            return Err(FileToolError::ResponseItemTooLarge {
                                kind: "grep_match",
                                required: next_response_bytes,
                                maximum: limits.response_bytes(),
                            });
                        }
                        file_truncated = true;
                        break;
                    }
                    file_response_bytes = next_file_response_bytes;
                    file_matches.push(GrepMatch {
                        path: entry.path.clone(),
                        line_number,
                        byte_offset,
                        excerpt: text.to_string(),
                    });
                }
            }
            if file_truncated {
                invalid_file = probe_remainder(&mut reader, limits.read_chunk_bytes(), &control)?;
                break;
            }
        }

        if let Some(reason) = invalid_file {
            let skipped = GrepSkipped {
                path: entry.path,
                reason,
            };
            if !push_skip(&mut page, &mut response_bytes, skipped, limits)? {
                output_truncated = true;
                break 'files;
            }
            last_progress = Some(GrepCursor::after_file(entry_path.clone()));
            traversal_after = Some(entry_path);
        } else {
            response_bytes += file_response_bytes;
            let last_file_match = file_matches
                .last()
                .map(|found| (found.path.as_str().to_owned(), found.byte_offset));
            page.matches.extend(file_matches);
            if let Some((path, byte_offset)) = last_file_match {
                last_progress = Some(GrepCursor::within_file(
                    path,
                    byte_offset,
                    entry_walk_after.clone(),
                ));
            }
            for skipped in file_skips {
                if !push_skip(&mut page, &mut response_bytes, skipped, limits)? {
                    output_truncated = true;
                    break 'files;
                }
                last_progress = Some(GrepCursor::after_file(entry_path.clone()));
            }
            if file_truncated {
                page.truncated = true;
                output_truncated = true;
                break;
            }
            last_progress = Some(GrepCursor::after_file(entry_path.clone()));
            traversal_after = Some(entry_path);
        }
    }

    if output_truncated {
        page.next_cursor = last_progress;
    } else if walk_truncated {
        page.truncated = true;
        page.limit_reached = walk_limit_reached;
        page.next_cursor =
            walk_next_cursor.map(|cursor| GrepCursor::after_file(cursor.as_str().to_owned()));
    }
    if page.truncated && page.next_cursor.is_none() {
        return Err(FileToolError::PaginationCannotAdvance { operation: "grep" });
    }
    Ok(page)
}

fn push_skip(
    page: &mut GrepPage,
    response_bytes: &mut usize,
    skipped: GrepSkipped,
    limits: ValidatedLimits,
) -> Result<bool, FileToolError> {
    let item_bytes = skipped
        .path
        .as_str()
        .len()
        .saturating_mul(12)
        .saturating_add(RESPONSE_SKIP_OVERHEAD_BYTES);
    let Some(next_response_bytes) = response_bytes.checked_add(item_bytes) else {
        if page.matches.is_empty() && page.skipped.is_empty() {
            return Err(FileToolError::ResponseItemTooLarge {
                kind: "grep_skip",
                required: usize::MAX,
                maximum: limits.response_bytes(),
            });
        }
        page.truncated = true;
        page.limit_reached = true;
        return Ok(false);
    };
    if next_response_bytes > limits.response_bytes() {
        if page.matches.is_empty() && page.skipped.is_empty() {
            return Err(FileToolError::ResponseItemTooLarge {
                kind: "grep_skip",
                required: next_response_bytes,
                maximum: limits.response_bytes(),
            });
        }
        page.truncated = true;
        page.limit_reached = true;
        return Ok(false);
    }
    *response_bytes = next_response_bytes;
    page.skipped.push(skipped);
    Ok(true)
}

impl GrepCursor {
    fn within_file(path: String, byte_offset: u64, walk_after: Option<String>) -> Self {
        Self {
            path,
            byte_offset: Some(byte_offset),
            walk_after,
        }
    }

    fn after_file(path: String) -> Self {
        Self {
            walk_after: Some(path.clone()),
            path,
            byte_offset: None,
        }
    }

    fn resume_walk_cursor(&self) -> Option<WalkCursor> {
        if self.byte_offset.is_some() {
            self.walk_after.clone().map(WalkCursor::after)
        } else {
            Some(WalkCursor::after(self.path.clone()))
        }
    }

    pub fn path(&self) -> &str {
        &self.path
    }

    pub const fn byte_offset(&self) -> Option<u64> {
        self.byte_offset
    }

    pub fn walk_after(&self) -> Option<&str> {
        self.walk_after.as_deref()
    }
}

fn validate_request(request: &GrepRequest, limits: ValidatedLimits) -> Result<(), FileToolError> {
    if request.query.len() > MAX_PATTERN_BYTES {
        return Err(FileToolError::InvalidPattern {
            mode: "grep",
            message: format!("pattern exceeds the {MAX_PATTERN_BYTES}-byte limit"),
        });
    }
    for (name, value, maximum) in [
        ("max_files", request.max_files, limits.walk_entries()),
        ("max_matches", request.max_matches, limits.walk_entries()),
        (
            "max_line_bytes",
            request.max_line_bytes,
            limits.response_bytes(),
        ),
    ] {
        if value == 0 || value > maximum {
            return Err(FileToolError::InvalidPattern {
                mode: "grep_limit",
                message: format!("{name} must be between 1 and {maximum}"),
            });
        }
    }
    Ok(())
}

fn is_after_cursor(cursor: Option<&GrepCursor>, path: &str, byte_offset: u64) -> bool {
    match cursor {
        None => true,
        Some(cursor) => match cursor.byte_offset {
            Some(cursor_offset) if path == cursor.path => byte_offset > cursor_offset,
            _ => true,
        },
    }
}

struct BoundedLine {
    bytes: Vec<u8>,
    consumed: usize,
    too_long: bool,
}

fn read_bounded_line(
    reader: &mut impl BufRead,
    maximum: usize,
    control: &OperationControl,
) -> Result<Option<BoundedLine>, FileToolError> {
    let mut bytes = Vec::new();
    let mut consumed = 0usize;
    loop {
        control.check()?;
        let available = reader.fill_buf().map_err(|error| FileToolError::Backend {
            operation: "grep_read",
            path: "<stream>".to_string(),
            message: error.to_string(),
        })?;
        if available.is_empty() {
            return if consumed == 0 {
                Ok(None)
            } else {
                Ok(Some(BoundedLine {
                    bytes,
                    consumed,
                    too_long: false,
                }))
            };
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let take = newline.map_or(available.len(), |index| index + 1);
        if bytes.len().saturating_add(take) > maximum {
            return Ok(Some(BoundedLine {
                bytes: Vec::new(),
                consumed,
                too_long: true,
            }));
        }
        bytes.extend_from_slice(&available[..take]);
        reader.consume(take);
        consumed = consumed.saturating_add(take);
        if newline.is_some() {
            return Ok(Some(BoundedLine {
                bytes,
                consumed,
                too_long: false,
            }));
        }
    }
}

fn probe_remainder(
    reader: &mut impl BufRead,
    maximum: usize,
    control: &OperationControl,
) -> Result<Option<SkipReason>, FileToolError> {
    let mut bytes = Vec::new();
    while bytes.len() < maximum {
        control.check()?;
        let available = reader.fill_buf().map_err(|error| FileToolError::Backend {
            operation: "grep_read",
            path: "<stream>".to_string(),
            message: error.to_string(),
        })?;
        if available.is_empty() {
            break;
        }
        let take = available.len().min(maximum - bytes.len());
        if available[..take].contains(&0) {
            return Ok(Some(SkipReason::BinaryContent));
        }
        bytes.extend_from_slice(&available[..take]);
        reader.consume(take);
    }
    match std::str::from_utf8(&bytes) {
        Ok(_) => Ok(None),
        Err(error) if error.error_len().is_none() => Ok(None),
        Err(_) => Ok(Some(SkipReason::InvalidUtf8)),
    }
}
