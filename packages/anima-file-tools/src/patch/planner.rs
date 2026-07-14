// SPDX-License-Identifier: Apache-2.0
// Sequence-seeking behavior adapted from OpenAI Codex
// codex-rs/apply-patch/src/seek_sequence.rs at
// 9e552e9d15ba52bed7077d5357f3e18e330f8f38.

use std::collections::BTreeMap;
use std::path::Path;

use crate::MutationAtomicity;

use super::{Patch, PatchChunk, PatchError, PatchOperation, PatchPath};

pub trait PatchSnapshot {
    fn read_text(&self, path: &str) -> Result<Option<String>, PatchError>;

    fn file_entry_exists(&self, path: &str) -> Result<bool, PatchError> {
        Ok(self.read_text(path)?.is_some())
    }

    fn canonical_key(&self, path: &str) -> Result<String, PatchError> {
        Ok(path.to_string())
    }

    fn canonical_entry_key(&self, path: &str) -> Result<String, PatchError> {
        self.canonical_key(path)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PatchPlan {
    pub mutations: Vec<PlannedMutation>,
    pub atomicity: MutationAtomicity,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PlannedMutation {
    Write {
        path: PatchPath,
        content: String,
        remove_source: Option<PatchPath>,
    },
    Delete {
        path: PatchPath,
    },
}

pub fn plan_patch<S: PatchSnapshot + ?Sized>(
    snapshot: &S,
    patch: &Patch,
    atomicity: MutationAtomicity,
) -> Result<PatchPlan, PatchError> {
    let mut virtual_files = BTreeMap::<String, Option<String>>::new();
    let mut mutations = Vec::new();

    for operation in &patch.operations {
        match operation {
            PatchOperation::Add { path, content } => {
                if load(snapshot, &mut virtual_files, path)?.is_some() {
                    return Err(PatchError::PathAlreadyExists {
                        path: path.as_str().to_string(),
                    });
                }
                store(snapshot, &mut virtual_files, path, Some(content.clone()))?;
                mutations.push(PlannedMutation::Write {
                    path: path.clone(),
                    content: content.clone(),
                    remove_source: None,
                });
            }
            PatchOperation::Delete { path } => {
                if !file_entry_exists(snapshot, &virtual_files, path)? {
                    return Err(PatchError::MissingPath {
                        path: path.as_str().to_string(),
                    });
                }
                store_entry(snapshot, &mut virtual_files, path, None)?;
                mutations.push(PlannedMutation::Delete { path: path.clone() });
            }
            PatchOperation::Update {
                source,
                destination,
                chunks,
            } => {
                let Some(content) = load(snapshot, &mut virtual_files, source)? else {
                    return Err(PatchError::MissingPath {
                        path: source.as_str().to_string(),
                    });
                };
                let content = if chunks.is_empty() {
                    content
                } else {
                    apply_chunks(source, content, chunks)?
                };
                let target = destination.as_ref().unwrap_or(source);
                let remove_source = if target != source {
                    if load(snapshot, &mut virtual_files, target)?.is_some() {
                        return Err(PatchError::PathAlreadyExists {
                            path: target.as_str().to_string(),
                        });
                    }
                    store(snapshot, &mut virtual_files, target, Some(content.clone()))?;
                    store_entry(snapshot, &mut virtual_files, source, None)?;
                    Some(source.clone())
                } else {
                    store(snapshot, &mut virtual_files, target, Some(content.clone()))?;
                    None
                };
                mutations.push(PlannedMutation::Write {
                    path: target.clone(),
                    content,
                    remove_source,
                });
            }
        }
    }

    Ok(PatchPlan {
        mutations,
        atomicity,
    })
}

fn load<S: PatchSnapshot + ?Sized>(
    snapshot: &S,
    virtual_files: &mut BTreeMap<String, Option<String>>,
    path: &PatchPath,
) -> Result<Option<String>, PatchError> {
    let key = snapshot.canonical_key(path.as_str())?;
    if let Some(content) = virtual_files.get(&key) {
        return Ok(content.clone());
    }
    let content = snapshot.read_text(path.as_str())?;
    virtual_files.insert(key, content.clone());
    Ok(content)
}

fn file_entry_exists<S: PatchSnapshot + ?Sized>(
    snapshot: &S,
    virtual_files: &BTreeMap<String, Option<String>>,
    path: &PatchPath,
) -> Result<bool, PatchError> {
    let key = snapshot.canonical_entry_key(path.as_str())?;
    if let Some(content) = virtual_files.get(&key) {
        return Ok(content.is_some());
    }
    snapshot.file_entry_exists(path.as_str())
}

fn store<S: PatchSnapshot + ?Sized>(
    snapshot: &S,
    virtual_files: &mut BTreeMap<String, Option<String>>,
    path: &PatchPath,
    content: Option<String>,
) -> Result<(), PatchError> {
    let key = snapshot.canonical_key(path.as_str())?;
    store_key(virtual_files, path, key, content)
}

fn store_entry<S: PatchSnapshot + ?Sized>(
    snapshot: &S,
    virtual_files: &mut BTreeMap<String, Option<String>>,
    path: &PatchPath,
    content: Option<String>,
) -> Result<(), PatchError> {
    let key = snapshot.canonical_entry_key(path.as_str())?;
    store_key(virtual_files, path, key, content)
}

fn store_key(
    virtual_files: &mut BTreeMap<String, Option<String>>,
    path: &PatchPath,
    key: String,
    content: Option<String>,
) -> Result<(), PatchError> {
    if content.is_some()
        && virtual_files.iter().any(|(existing, existing_content)| {
            existing_content.is_some()
                && existing != &key
                && (Path::new(existing).starts_with(Path::new(&key))
                    || Path::new(&key).starts_with(Path::new(existing)))
        })
    {
        return Err(PatchError::PathAlreadyExists {
            path: path.as_str().to_string(),
        });
    }
    virtual_files.insert(key, content);
    Ok(())
}

fn apply_chunks(
    path: &PatchPath,
    content: String,
    chunks: &[PatchChunk],
) -> Result<String, PatchError> {
    let had_trailing_newline = content.ends_with('\n') || content.ends_with('\r');
    let newline = if content.contains("\r\n") {
        "\r\n"
    } else {
        "\n"
    };
    let normalized = content.replace("\r\n", "\n").replace('\r', "\n");
    let mut lines = normalized
        .strip_suffix('\n')
        .unwrap_or(&normalized)
        .split('\n')
        .map(str::to_string)
        .collect::<Vec<_>>();
    if normalized.is_empty() {
        lines.clear();
    }
    let mut cursor = 0usize;

    for chunk in chunks {
        let start = if let Some(context) = &chunk.context {
            let Some(relative) = lines[cursor..].iter().position(|line| line == context) else {
                return Err(PatchError::ContextNotFound {
                    path: path.as_str().to_string(),
                    context: context.clone(),
                });
            };
            cursor + relative + 1
        } else {
            cursor
        };
        let position = if chunk.old_lines.is_empty() {
            if chunk.context.is_some() {
                start
            } else {
                lines.len()
            }
        } else {
            find_sequence(&lines, &chunk.old_lines, start).ok_or_else(|| {
                PatchError::HunkNotFound {
                    path: path.as_str().to_string(),
                }
            })?
        };
        let end = position + chunk.old_lines.len();
        if chunk.end_of_file && end != lines.len() {
            return Err(PatchError::EndOfFileMismatch {
                path: path.as_str().to_string(),
            });
        }
        lines.splice(position..end, chunk.new_lines.clone());
        cursor = position + chunk.new_lines.len();
    }

    if lines.is_empty() {
        Ok(String::new())
    } else if had_trailing_newline {
        Ok(format!("{}{}", lines.join(newline), newline))
    } else {
        Ok(lines.join(newline))
    }
}

fn find_sequence(lines: &[String], needle: &[String], start: usize) -> Option<usize> {
    if needle.is_empty() || start > lines.len() || needle.len() > lines.len() {
        return None;
    }
    (start..=lines.len() - needle.len())
        .find(|index| lines[*index..*index + needle.len()] == *needle)
}
