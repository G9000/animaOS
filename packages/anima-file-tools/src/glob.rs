// SPDX-License-Identifier: Apache-2.0

use globset::GlobBuilder;

use crate::{
    walk_page, BackendPath, EntryKind, FileToolError, OperationControl, ValidatedLimits,
    WalkBackend, WalkCursor, WalkOptions, MAX_PATTERN_BYTES,
};

// Includes room for backend-enriched identity metadata in the public result.
const RESPONSE_ITEM_OVERHEAD_BYTES: usize = 192;
const RESPONSE_PAGE_OVERHEAD_BYTES: usize = 64;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GlobCursor {
    after: String,
}

impl GlobCursor {
    pub fn after(path: impl Into<String>) -> Self {
        Self { after: path.into() }
    }

    pub fn as_str(&self) -> &str {
        &self.after
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct GlobRequest {
    pub root: BackendPath,
    pub pattern: String,
    pub cursor: Option<GlobCursor>,
    pub max_results: usize,
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct GlobPage {
    pub matches: Vec<BackendPath>,
    pub next_cursor: Option<GlobCursor>,
    pub truncated: bool,
    pub limit_reached: bool,
}

pub fn glob<B: WalkBackend + ?Sized>(
    backend: &B,
    request: GlobRequest,
    limits: ValidatedLimits,
    control: OperationControl,
) -> Result<GlobPage, FileToolError> {
    control.check()?;
    if request.max_results == 0 || request.max_results > limits.walk_entries() {
        return Err(FileToolError::InvalidPattern {
            mode: "glob_limit",
            message: format!(
                "max_results must be between 1 and {}",
                limits.walk_entries()
            ),
        });
    }
    if request.pattern.len() > MAX_PATTERN_BYTES {
        return Err(FileToolError::InvalidPattern {
            mode: "glob",
            message: format!("pattern exceeds the {MAX_PATTERN_BYTES}-byte limit"),
        });
    }
    let pattern = request.pattern.replace('\\', "/");
    let matcher = GlobBuilder::new(&pattern)
        .literal_separator(true)
        .backslash_escape(false)
        .build()
        .map_err(|error| FileToolError::InvalidPattern {
            mode: "glob",
            message: error.to_string(),
        })?
        .compile_matcher();

    let walk_cursor = request
        .cursor
        .as_ref()
        .map(|cursor| WalkCursor::after(cursor.after.clone()));
    let root = request.root;
    let walk = walk_page(
        backend,
        root.clone(),
        WalkOptions {
            page_size: limits.walk_entries(),
            cursor: walk_cursor,
            include_directories: false,
        },
        limits,
        control.clone(),
    )
    .map_err(|error| match error {
        FileToolError::PaginationCannotAdvance { .. } => {
            FileToolError::PaginationCannotAdvance { operation: "glob" }
        }
        error => error,
    })?;
    let walk_truncated = walk.truncated;
    let walk_limit_reached = walk.limit_reached;
    let walk_next_cursor = walk.next_cursor;
    let mut page = GlobPage::default();
    let mut response_bytes = RESPONSE_PAGE_OVERHEAD_BYTES;
    let mut output_truncated = false;

    for entry in walk.entries {
        control.check()?;
        if entry.kind != EntryKind::File
            || !matcher.is_match(relative_path(root.as_str(), entry.path.as_str()))
        {
            continue;
        }
        if page.matches.len() == request.max_results {
            page.truncated = true;
            output_truncated = true;
            break;
        }
        let item_bytes = entry
            .path
            .as_str()
            .len()
            .saturating_mul(12)
            .saturating_add(RESPONSE_ITEM_OVERHEAD_BYTES);
        let Some(next_response_bytes) = response_bytes.checked_add(item_bytes) else {
            if page.matches.is_empty() {
                return Err(FileToolError::ResponseItemTooLarge {
                    kind: "glob",
                    required: usize::MAX,
                    maximum: limits.response_bytes(),
                });
            }
            page.truncated = true;
            output_truncated = true;
            break;
        };
        if next_response_bytes > limits.response_bytes() {
            if page.matches.is_empty() {
                return Err(FileToolError::ResponseItemTooLarge {
                    kind: "glob",
                    required: next_response_bytes,
                    maximum: limits.response_bytes(),
                });
            }
            page.truncated = true;
            output_truncated = true;
            break;
        }
        response_bytes = next_response_bytes;
        page.matches.push(entry.path);
    }

    if output_truncated {
        page.next_cursor = page.matches.last().map(|path| GlobCursor {
            after: path.as_str().to_string(),
        });
    } else if walk_truncated {
        page.truncated = true;
        page.limit_reached = walk_limit_reached;
        page.next_cursor = walk_next_cursor.map(|cursor| GlobCursor {
            after: cursor.as_str().to_string(),
        });
    }
    if page.truncated && page.next_cursor.is_none() {
        return Err(FileToolError::PaginationCannotAdvance { operation: "glob" });
    }
    Ok(page)
}

fn relative_path(root: &str, path: &str) -> String {
    let root = root.replace('\\', "/");
    let path = path.replace('\\', "/");
    let root = root.trim_end_matches('/');
    path.strip_prefix(root)
        .and_then(|suffix| {
            suffix
                .strip_prefix('/')
                .or_else(|| suffix.is_empty().then_some(""))
        })
        .unwrap_or(&path)
        .to_string()
}
