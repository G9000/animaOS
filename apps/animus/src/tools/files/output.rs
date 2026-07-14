use std::path::Path;

use anima_file_tools::{
    DirectoryListing, EntryKind, GlobPage, GrepPage, SkipReason, TextReadPage, MAX_RESPONSE_BYTES,
};

use crate::permissions::PermissionPolicy;

pub(super) fn text(page: TextReadPage) -> String {
    page.lines
        .into_iter()
        .map(|line| format!("{}: {}", line.number, line.text))
        .collect::<Vec<_>>()
        .join("\n")
}

pub(super) fn grep(page: GrepPage, policy: &PermissionPolicy, limit: usize) -> String {
    let match_count = page.matches.len();
    let mut lines = page
        .matches
        .into_iter()
        .map(|found| {
            format!(
                "{}:{}:{}",
                workspace_path(policy, Path::new(found.path.as_str())),
                found.line_number,
                found.excerpt
            )
        })
        .collect::<Vec<_>>();
    lines.extend(page.skipped.into_iter().map(|skipped| {
        let reason = match skipped.reason {
            SkipReason::BinaryContent => "binary content",
            SkipReason::InvalidUtf8 => "invalid UTF-8",
            SkipReason::LineTooLong => "line exceeds byte limit",
        };
        format!(
            "... skipped {}: {reason}",
            workspace_path(policy, Path::new(skipped.path.as_str()))
        )
    }));
    if page.truncated {
        lines.push(truncation_message(match_count, limit, page.limit_reached));
    }
    lines.join("\n")
}

pub(super) fn glob(page: GlobPage, policy: &PermissionPolicy, limit: usize) -> String {
    let match_count = page.matches.len();
    let mut lines = page
        .matches
        .into_iter()
        .map(|path| workspace_path(policy, Path::new(path.as_str())))
        .collect::<Vec<_>>();
    if page.truncated {
        lines.push(truncation_message(match_count, limit, page.limit_reached));
    }
    lines.join("\n")
}

fn truncation_message(match_count: usize, requested_limit: usize, limit_reached: bool) -> String {
    if match_count == requested_limit {
        format!("... truncated after {requested_limit} matches")
    } else if limit_reached {
        "... scan stopped at a production safety limit".to_string()
    } else {
        "... more results are available".to_string()
    }
}

pub(super) fn directory(mut listing: DirectoryListing, limit: usize) -> String {
    const TRUNCATION_RESERVE_BYTES: usize = 64;

    listing
        .entries
        .sort_by(|left, right| left.path.as_str().cmp(right.path.as_str()));
    let mut lines = Vec::new();
    let mut response_bytes = 0usize;
    let mut response_limited = false;
    for entry in listing.entries {
        let Some(line) = (|| {
            let name = Path::new(entry.path.as_str())
                .file_name()?
                .to_string_lossy();
            let marker = if entry.metadata.kind == EntryKind::Directory {
                "/"
            } else {
                ""
            };
            Some(format!("{name}{marker}"))
        })() else {
            continue;
        };
        let next_bytes = line.len() + usize::from(!lines.is_empty());
        if response_bytes.saturating_add(next_bytes)
            > MAX_RESPONSE_BYTES.saturating_sub(TRUNCATION_RESERVE_BYTES)
        {
            response_limited = true;
            break;
        }
        response_bytes += next_bytes;
        lines.push(line);
    }
    if listing.truncated || response_limited {
        lines.push(if listing.truncated && lines.len() == limit {
            format!("... truncated after {limit} entries")
        } else {
            "... directory output stopped at a production safety limit".to_string()
        });
    }
    lines.join("\n")
}

pub(super) fn workspace_path(policy: &PermissionPolicy, path: &Path) -> String {
    path.strip_prefix(policy.workspace())
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}
