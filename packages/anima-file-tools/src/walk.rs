// SPDX-License-Identifier: Apache-2.0
// Bounded traversal pattern adapted from OpenAI Codex codex-rs/file-system/src/lib.rs at
// 9e552e9d15ba52bed7077d5357f3e18e330f8f38; implementation rewritten for backend cursors.

use crate::{
    BackendPath, DirectoryEntry, EntryKind, FileToolError, OperationControl, ValidatedLimits,
    WalkBackend, MAX_WALK_ERRORS, MAX_WALK_ERROR_MESSAGE_BYTES,
};

const RESPONSE_ITEM_OVERHEAD_BYTES: usize = 64;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WalkCursor {
    after: String,
}

impl WalkCursor {
    pub fn after(path: impl Into<String>) -> Self {
        Self { after: path.into() }
    }

    pub fn as_str(&self) -> &str {
        &self.after
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WalkOptions {
    pub page_size: usize,
    pub cursor: Option<WalkCursor>,
    pub include_directories: bool,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WalkEntry {
    pub path: BackendPath,
    pub kind: EntryKind,
    pub is_symlink: bool,
    pub size: u64,
    pub depth: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WalkError {
    pub path: BackendPath,
    pub message: String,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct WalkPage {
    pub entries: Vec<WalkEntry>,
    pub errors: Vec<WalkError>,
    pub next_cursor: Option<WalkCursor>,
    pub truncated: bool,
    pub limit_reached: bool,
}

struct PendingEntry {
    entry: DirectoryEntry,
    depth: usize,
}

pub fn walk_page<B: WalkBackend + ?Sized>(
    backend: &B,
    root: BackendPath,
    options: WalkOptions,
    limits: ValidatedLimits,
    control: OperationControl,
) -> Result<WalkPage, FileToolError> {
    control.check()?;
    ensure_backend(backend, &root)?;
    if options.page_size == 0 {
        return Err(FileToolError::ResponseLimitExceeded {
            requested: 0,
            maximum: limits.response_bytes(),
        });
    }

    let root_metadata = backend.metadata(root.as_str())?;
    if root_metadata.kind != EntryKind::Directory || root_metadata.is_symlink {
        return Ok(WalkPage::default());
    }

    let mut page = WalkPage::default();
    let mut response_bytes = 0usize;
    let mut examined_entries = 0usize;
    let mut visited_directories = 1usize;
    let mut resumable = true;
    let mut stack = Vec::new();
    if push_children(
        backend,
        &root,
        1,
        &mut stack,
        &mut page.errors,
        &mut response_bytes,
        limits,
    )? {
        page.truncated = true;
        page.limit_reached = true;
        resumable = false;
    }
    let cursor = options.cursor.as_ref().map(|cursor| cursor.after.as_str());

    while let Some(pending) = stack.pop() {
        control.check()?;
        if examined_entries == limits.walk_entries() {
            page.truncated = true;
            page.limit_reached = true;
            resumable = false;
            break;
        }
        examined_entries += 1;
        ensure_backend(backend, &pending.entry.path)?;

        let metadata = pending.entry.metadata;
        if metadata.kind == EntryKind::Directory
            && !metadata.is_symlink
            && pending.depth < limits.walk_depth()
        {
            if visited_directories == limits.walk_directories() {
                page.truncated = true;
                page.limit_reached = true;
                resumable = false;
            } else {
                visited_directories += 1;
                if push_children(
                    backend,
                    &pending.entry.path,
                    pending.depth + 1,
                    &mut stack,
                    &mut page.errors,
                    &mut response_bytes,
                    limits,
                )? {
                    page.truncated = true;
                    page.limit_reached = true;
                    resumable = false;
                }
            }
        } else if metadata.kind == EntryKind::Directory
            && !metadata.is_symlink
            && pending.depth >= limits.walk_depth()
        {
            page.truncated = true;
            page.limit_reached = true;
            resumable = false;
        }

        let visible = metadata.kind != EntryKind::Directory || options.include_directories;
        let after_cursor = cursor.map_or(true, |cursor| pending.entry.path.as_str() > cursor);
        if !visible || !after_cursor {
            continue;
        }

        let item_bytes = pending
            .entry
            .path
            .as_str()
            .len()
            .saturating_add(RESPONSE_ITEM_OVERHEAD_BYTES);
        let Some(next_response_bytes) = response_bytes.checked_add(item_bytes) else {
            page.truncated = true;
            page.limit_reached = true;
            break;
        };
        if next_response_bytes > limits.response_bytes() {
            page.truncated = true;
            page.limit_reached = true;
            break;
        }
        response_bytes = next_response_bytes;

        page.entries.push(WalkEntry {
            path: pending.entry.path,
            kind: metadata.kind,
            is_symlink: metadata.is_symlink,
            size: metadata.size,
            depth: pending.depth,
        });
        if page.entries.len() > options.page_size {
            page.entries.pop();
            page.truncated = true;
            break;
        }
    }

    if page.truncated && resumable {
        page.next_cursor = page.entries.last().map(|entry| WalkCursor {
            after: entry.path.as_str().to_string(),
        });
    }
    Ok(page)
}

fn ensure_backend<B: WalkBackend + ?Sized>(
    backend: &B,
    path: &BackendPath,
) -> Result<(), FileToolError> {
    let selected_backend = backend.capabilities().backend();
    if path.backend() != selected_backend {
        return Err(FileToolError::BackendMismatch {
            path_backend: path.backend(),
            selected_backend,
        });
    }
    Ok(())
}

fn push_children<B: WalkBackend + ?Sized>(
    backend: &B,
    directory: &BackendPath,
    depth: usize,
    stack: &mut Vec<PendingEntry>,
    errors: &mut Vec<WalkError>,
    response_bytes: &mut usize,
    limits: ValidatedLimits,
) -> Result<bool, FileToolError> {
    let listing = match backend.read_directory(directory.as_str()) {
        Ok(listing) => listing,
        Err(error) => {
            if errors.len() == MAX_WALK_ERRORS {
                return Ok(true);
            }
            let message = truncate_utf8(&error.to_string(), MAX_WALK_ERROR_MESSAGE_BYTES);
            let item_bytes = directory
                .as_str()
                .len()
                .saturating_add(message.len())
                .saturating_add(RESPONSE_ITEM_OVERHEAD_BYTES);
            let Some(next_response_bytes) = response_bytes.checked_add(item_bytes) else {
                return Ok(true);
            };
            if next_response_bytes > limits.response_bytes() {
                return Ok(true);
            }
            *response_bytes = next_response_bytes;
            errors.push(WalkError {
                path: directory.clone(),
                message,
            });
            return Ok(false);
        }
    };
    let listing_truncated = listing.truncated;
    let mut children = listing.entries;
    children.sort_by(|left, right| left.path.as_str().cmp(right.path.as_str()));
    stack.extend(
        children
            .into_iter()
            .rev()
            .map(|entry| PendingEntry { entry, depth }),
    );
    Ok(listing_truncated)
}

fn truncate_utf8(value: &str, maximum_bytes: usize) -> String {
    if value.len() <= maximum_bytes {
        return value.to_string();
    }
    let mut end = maximum_bytes;
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    value[..end].to_string()
}
