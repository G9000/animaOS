// SPDX-License-Identifier: Apache-2.0
// Adapted from OpenAI Codex codex-rs/apply-patch/src/parser.rs at
// 9e552e9d15ba52bed7077d5357f3e18e330f8f38; rewritten for backend-neutral paths.

use thiserror::Error;

use crate::{MAX_BACKEND_PATH_BYTES, MAX_RESPONSE_BYTES};

pub const MAX_PATCH_BYTES: usize = MAX_RESPONSE_BYTES;
pub const MAX_PATCH_OPERATIONS: usize = 1_024;

const BEGIN: &str = "*** Begin Patch";
const END: &str = "*** End Patch";
const ADD: &str = "*** Add File: ";
const DELETE: &str = "*** Delete File: ";
const UPDATE: &str = "*** Update File: ";
const MOVE: &str = "*** Move to: ";
const END_OF_FILE: &str = "*** End of File";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Patch {
    pub operations: Vec<PatchOperation>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PatchOperation {
    Add {
        path: PatchPath,
        content: String,
    },
    Delete {
        path: PatchPath,
    },
    Update {
        source: PatchPath,
        destination: Option<PatchPath>,
        chunks: Vec<PatchChunk>,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PatchChunk {
    pub context: Option<String>,
    pub old_lines: Vec<String>,
    pub new_lines: Vec<String>,
    pub end_of_file: bool,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct PatchPath(String);

impl PatchPath {
    fn parse(raw: &str, line: usize) -> Result<Self, PatchError> {
        let raw = raw.trim();
        let normalized = raw.replace('\\', "/");
        if normalized.len() > MAX_BACKEND_PATH_BYTES {
            return Err(PatchError::InvalidPath {
                path: "<oversized path>".to_string(),
                line,
            });
        }
        let has_drive_prefix = normalized
            .as_bytes()
            .get(1)
            .is_some_and(|byte| *byte == b':');
        let is_corefs_form = normalized
            .get(..7)
            .is_some_and(|prefix| prefix.eq_ignore_ascii_case("corefs:"));
        if normalized.is_empty()
            || normalized.contains('\0')
            || normalized.starts_with('/')
            || has_drive_prefix
            || is_corefs_form
            || normalized.contains("://")
            || normalized
                .split('/')
                .any(|component| component == "." || component == ".." || component.is_empty())
        {
            return Err(PatchError::InvalidPath {
                path: raw.to_string(),
                line,
            });
        }
        Ok(Self(normalized))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Debug, Eq, Error, PartialEq)]
pub enum PatchError {
    #[error("invalid patch at line {line}: {message}")]
    Parse { line: usize, message: String },
    #[error("invalid relative patch path at line {line}: {path}")]
    InvalidPath { path: String, line: usize },
    #[error("patch path does not exist: {path}")]
    MissingPath { path: String },
    #[error("patch path already exists: {path}")]
    PathAlreadyExists { path: String },
    #[error("patch context was not found in {path}: {context}")]
    ContextNotFound { path: String, context: String },
    #[error("patch hunk does not match {path}")]
    HunkNotFound { path: String },
    #[error("patch hunk for {path} was marked end-of-file but did not match the file tail")]
    EndOfFileMismatch { path: String },
    #[error("failed to inspect {path}: {message}")]
    Snapshot { path: String, message: String },
}

pub fn parse_patch(input: &str) -> Result<Patch, PatchError> {
    if input.len() > MAX_PATCH_BYTES {
        return Err(parse_error(
            1,
            format!("patch exceeds the {MAX_PATCH_BYTES}-byte limit"),
        ));
    }
    let input = input.trim();
    let lines = input
        .lines()
        .map(|line| line.strip_suffix('\r').unwrap_or(line))
        .collect::<Vec<_>>();
    if lines.first().map(|line| line.trim()) != Some(BEGIN) {
        return Err(parse_error(1, "first line must be '*** Begin Patch'"));
    }
    if lines.last().map(|line| line.trim()) != Some(END) {
        return Err(parse_error(
            lines.len().max(1),
            "last line must be '*** End Patch'",
        ));
    }

    let mut operations = Vec::new();
    let mut index = 1usize;
    while index + 1 < lines.len() {
        if operations.len() == MAX_PATCH_OPERATIONS {
            return Err(parse_error(
                index + 1,
                format!("patch exceeds the {MAX_PATCH_OPERATIONS}-operation limit"),
            ));
        }
        let marker = lines[index].trim();
        if let Some(raw_path) = marker.strip_prefix(ADD) {
            let line = index + 1;
            let path = PatchPath::parse(raw_path, line)?;
            index += 1;
            let mut content = Vec::new();
            while index + 1 < lines.len() && !lines[index].starts_with("*** ") {
                let Some(added) = lines[index].strip_prefix('+') else {
                    return Err(parse_error(index + 1, "add-file lines must start with '+'"));
                };
                content.push(added);
                index += 1;
            }
            if content.is_empty() {
                return Err(parse_error(line, "add-file operation is empty"));
            }
            let trailing_newline = lines.get(index).copied() != Some(END_OF_FILE);
            if !trailing_newline {
                index += 1;
            }
            operations.push(PatchOperation::Add {
                path,
                content: if trailing_newline {
                    format!("{}\n", content.join("\n"))
                } else {
                    content.join("\n")
                },
            });
            continue;
        }
        if let Some(raw_path) = marker.strip_prefix(DELETE) {
            operations.push(PatchOperation::Delete {
                path: PatchPath::parse(raw_path, index + 1)?,
            });
            index += 1;
            continue;
        }
        if let Some(raw_path) = marker.strip_prefix(UPDATE) {
            let header_line = index + 1;
            let source = PatchPath::parse(raw_path, header_line)?;
            index += 1;
            let destination = if index + 1 < lines.len() {
                lines[index]
                    .trim()
                    .strip_prefix(MOVE)
                    .map(|path| PatchPath::parse(path, index + 1))
                    .transpose()?
            } else {
                None
            };
            if destination.is_some() {
                index += 1;
            }
            let mut chunks = Vec::new();
            while index + 1 < lines.len() && !lines[index].starts_with("*** ") {
                let chunk_line = index + 1;
                let header = lines[index].trim();
                let context = if header == "@@" {
                    None
                } else if let Some(context) = header.strip_prefix("@@ ") {
                    Some(context.to_string())
                } else {
                    return Err(parse_error(
                        chunk_line,
                        "update chunks must start with '@@'",
                    ));
                };
                index += 1;
                let mut old_lines = Vec::new();
                let mut new_lines = Vec::new();
                let mut changed = false;
                let mut end_of_file = false;
                while index + 1 < lines.len() {
                    let line = lines[index];
                    if line == END_OF_FILE {
                        end_of_file = true;
                        index += 1;
                        break;
                    }
                    if line.starts_with("@@") || line.starts_with("*** ") {
                        break;
                    }
                    let (prefix, value) = if let Some(value) = line.strip_prefix(' ') {
                        (' ', value.to_string())
                    } else if let Some(value) = line.strip_prefix('+') {
                        ('+', value.to_string())
                    } else if let Some(value) = line.strip_prefix('-') {
                        ('-', value.to_string())
                    } else {
                        return Err(parse_error(
                            index + 1,
                            "update lines require ' ', '+', or '-'",
                        ));
                    };
                    match prefix {
                        ' ' => {
                            old_lines.push(value.clone());
                            new_lines.push(value);
                        }
                        '+' => {
                            changed = true;
                            new_lines.push(value);
                        }
                        '-' => {
                            changed = true;
                            old_lines.push(value);
                        }
                        _ => {
                            return Err(parse_error(
                                index + 1,
                                "update lines require ' ', '+', or '-'",
                            ));
                        }
                    }
                    index += 1;
                }
                if !changed {
                    return Err(parse_error(chunk_line, "update chunk contains no change"));
                }
                chunks.push(PatchChunk {
                    context,
                    old_lines,
                    new_lines,
                    end_of_file,
                });
            }
            if chunks.is_empty() && destination.is_none() {
                return Err(parse_error(header_line, "update-file operation is empty"));
            }
            operations.push(PatchOperation::Update {
                source,
                destination,
                chunks,
            });
            continue;
        }
        return Err(parse_error(
            index + 1,
            "expected an add, delete, or update marker",
        ));
    }

    if operations.is_empty() {
        return Err(parse_error(1, "patch must contain at least one operation"));
    }
    Ok(Patch { operations })
}

fn parse_error(line: usize, message: impl Into<String>) -> PatchError {
    PatchError::Parse {
        line,
        message: message.into(),
    }
}
