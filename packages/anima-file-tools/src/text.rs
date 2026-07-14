// SPDX-License-Identifier: Apache-2.0
// Bounded text-reading pattern adapted from OpenAI Codex codex-rs/file-system/src/lib.rs at
// 9e552e9d15ba52bed7077d5357f3e18e330f8f38; implementation rewritten for line windows.

use std::io::{BufRead, BufReader};

use crate::{
    BackendPath, FileToolError, OperationControl, ReadBackend, TextReadIssue, ValidatedLimits,
};

const RESPONSE_ITEM_OVERHEAD_BYTES: usize = 32;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TextReadRequest {
    pub path: BackendPath,
    pub offset_lines: usize,
    pub max_lines: usize,
    pub max_line_bytes: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TextLine {
    pub number: usize,
    pub text: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct TextReadPage {
    pub lines: Vec<TextLine>,
    pub next_line_offset: Option<usize>,
    pub truncated: bool,
}

pub fn read_text_lines<B: ReadBackend + ?Sized>(
    backend: &B,
    request: TextReadRequest,
    limits: ValidatedLimits,
    control: OperationControl,
) -> Result<TextReadPage, FileToolError> {
    control.check()?;
    let selected_backend = backend.capabilities().backend();
    if request.path.backend() != selected_backend {
        return Err(FileToolError::BackendMismatch {
            path_backend: request.path.backend(),
            selected_backend,
        });
    }
    if request.max_lines == 0
        || request.max_lines > limits.walk_entries()
        || request.max_line_bytes == 0
        || request.max_line_bytes > limits.response_bytes()
    {
        return Err(FileToolError::InvalidPattern {
            mode: "text_read_limit",
            message: format!(
                "max_lines must be between 1 and {}, and max_line_bytes between 1 and {}",
                limits.walk_entries(),
                limits.response_bytes()
            ),
        });
    }

    let mut reader = BufReader::new(backend.open_read(request.path.as_str())?);
    let mut page = TextReadPage::default();
    let mut line_index = 0usize;
    let mut response_bytes = 0usize;

    while let Some(line) = read_line(&mut reader, request.max_line_bytes, &control)? {
        if line.too_long {
            return Err(invalid_text(&request.path, TextReadIssue::LineTooLong));
        }
        if line.bytes.contains(&0) {
            return Err(invalid_text(&request.path, TextReadIssue::BinaryContent));
        }
        let text = std::str::from_utf8(&line.bytes)
            .map_err(|_| invalid_text(&request.path, TextReadIssue::InvalidUtf8))?
            .trim_end_matches(['\r', '\n']);

        if line_index >= request.offset_lines {
            if page.lines.len() == request.max_lines {
                page.truncated = true;
                page.next_line_offset = Some(line_index);
                break;
            }
            let item_bytes = text
                .len()
                .checked_add(RESPONSE_ITEM_OVERHEAD_BYTES)
                .and_then(|size| response_bytes.checked_add(size))
                .ok_or(FileToolError::ResponseLimitExceeded {
                    requested: usize::MAX,
                    maximum: limits.response_bytes(),
                })?;
            if item_bytes > limits.response_bytes() {
                page.truncated = true;
                page.next_line_offset = Some(line_index);
                break;
            }
            response_bytes = item_bytes;
            page.lines.push(TextLine {
                number: line_index + 1,
                text: text.to_string(),
            });
        }
        line_index = line_index.saturating_add(1);
    }

    Ok(page)
}

fn invalid_text(path: &BackendPath, reason: TextReadIssue) -> FileToolError {
    FileToolError::InvalidTextContent {
        path: path.as_str().to_string(),
        reason,
    }
}

struct BoundedLine {
    bytes: Vec<u8>,
    too_long: bool,
}

fn read_line(
    reader: &mut impl BufRead,
    maximum: usize,
    control: &OperationControl,
) -> Result<Option<BoundedLine>, FileToolError> {
    let mut bytes = Vec::new();
    let mut consumed = 0usize;
    let mut too_long = false;
    loop {
        control.check()?;
        let available = reader.fill_buf().map_err(|error| FileToolError::Backend {
            operation: "read_text",
            path: "<stream>".to_string(),
            message: error.to_string(),
        })?;
        if available.is_empty() {
            return if consumed == 0 {
                Ok(None)
            } else {
                Ok(Some(BoundedLine { bytes, too_long }))
            };
        }
        let newline = available.iter().position(|byte| *byte == b'\n');
        let take = newline.map_or(available.len(), |index| index + 1);
        if !too_long {
            if bytes.len().saturating_add(take) > maximum {
                bytes.clear();
                too_long = true;
            } else {
                bytes.extend_from_slice(&available[..take]);
            }
        }
        reader.consume(take);
        consumed = consumed.saturating_add(take);
        if newline.is_some() {
            return Ok(Some(BoundedLine { bytes, too_long }));
        }
    }
}
