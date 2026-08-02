use std::fmt;

use unicode_normalization::UnicodeNormalization;

use crate::folders::MAX_PORTABLE_NAME_BYTES;
use sha2::{Digest, Sha256};

const MAX_LOGICAL_PATH_BYTES: usize = 32 * 1024;
const RESERVED_COMPONENTS: &[&str] = &[
    ".anima",
    ".corefs",
    "objects",
    "fs",
    "catalogs",
    "head",
    "validation_head",
    "manifest.json",
    "soul",
    "soul.db",
    "CUTOVER_RECEIPT",
    "CUTOVER_COMPLETE",
    "commit.lock",
];

/// A canonical root-relative CoreFS path.
///
/// The empty string names the selected catalog root. Paths never contain a
/// leading separator, a backend/URI prefix, or a host-native component.
#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct LogicalPath(String);

impl LogicalPath {
    pub fn parse(value: &str) -> Result<Self, LogicalPathError> {
        if value.len() > MAX_LOGICAL_PATH_BYTES {
            return Err(LogicalPathError::TooLong);
        }
        if value.is_empty() {
            return Ok(Self(String::new()));
        }
        if value.starts_with('/') || value.starts_with('\\') {
            return Err(LogicalPathError::Absolute);
        }
        if value.contains('\0') {
            return Err(LogicalPathError::Nul);
        }
        if value.contains('\\') {
            return Err(LogicalPathError::HostSeparator);
        }
        if has_uri_scheme(value) {
            return Err(LogicalPathError::ForeignBackend);
        }

        for component in value.split('/') {
            validate_component(component)?;
        }
        Ok(Self(value.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub(crate) fn join_component(&self, component: &str) -> Result<Self, LogicalPathError> {
        validate_component(component)?;
        let joined = if self.0.is_empty() {
            component.to_owned()
        } else {
            format!("{}/{}", self.0, component)
        };
        Self::parse(&joined)
    }
}

/// Maps one untrusted legacy display name into a deterministic logical component.
///
/// Migration callers must use this boundary instead of reproducing logical-path
/// rules in another language. Unsafe characters are byte-escaped, reserved
/// spellings are escaped, and overlong results receive a stable hash suffix.
pub fn map_migration_component(value: &str, stable_id: &str) -> Result<String, LogicalPathError> {
    let normalized: String = value.nfc().collect();
    let mut mapped = String::new();
    for character in normalized.chars() {
        if character == '/'
            || character == '\\'
            || character.is_control()
            || is_ambiguous_path_character(character)
        {
            for byte in character.to_string().as_bytes() {
                mapped.push_str(&format!("~{byte:02X}"));
            }
        } else {
            mapped.push(character);
        }
    }
    if mapped.is_empty() {
        mapped = format!("item-{stable_id}");
    }
    if mapped == "."
        || mapped == ".."
        || RESERVED_COMPONENTS
            .iter()
            .any(|reserved| mapped.eq_ignore_ascii_case(reserved))
    {
        mapped = mapped
            .as_bytes()
            .iter()
            .map(|byte| format!("~{byte:02X}"))
            .collect();
    }
    if mapped.len() > MAX_PORTABLE_NAME_BYTES {
        let suffix = format!("~{:x}", Sha256::digest(value.as_bytes()));
        let suffix = &suffix[..17];
        let available = MAX_PORTABLE_NAME_BYTES - suffix.len();
        while mapped.len() > available {
            mapped.pop();
        }
        mapped.push_str(suffix);
    }
    validate_component(&mapped)?;
    Ok(mapped)
}

impl fmt::Display for LogicalPath {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

fn validate_component(component: &str) -> Result<(), LogicalPathError> {
    if component.is_empty() {
        return Err(LogicalPathError::EmptyComponent);
    }
    if component == "." || component == ".." {
        return Err(LogicalPathError::Traversal);
    }
    if component.len() > MAX_PORTABLE_NAME_BYTES {
        return Err(LogicalPathError::ComponentTooLong);
    }
    if component.chars().any(char::is_control) {
        return Err(LogicalPathError::ControlCharacter);
    }
    if !component.nfc().eq(component.chars()) {
        return Err(LogicalPathError::NonCanonicalUnicode);
    }
    if component.chars().any(is_ambiguous_path_character) {
        return Err(LogicalPathError::AmbiguousUnicode);
    }
    if RESERVED_COMPONENTS
        .iter()
        .any(|reserved| component.eq_ignore_ascii_case(reserved))
    {
        return Err(LogicalPathError::ReservedComponent);
    }
    Ok(())
}

fn has_uri_scheme(value: &str) -> bool {
    let first_component = value.split('/').next().unwrap_or(value);
    let Some((scheme, _)) = first_component.split_once(':') else {
        return false;
    };
    !scheme.is_empty()
        && scheme.as_bytes()[0].is_ascii_alphabetic()
        && scheme
            .as_bytes()
            .iter()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'-' | b'.'))
}

fn is_ambiguous_path_character(value: char) -> bool {
    matches!(
        value,
        '\u{2044}'
            | '\u{2215}'
            | '\u{29f8}'
            | '\u{ff0f}'
            | '\u{ff3c}'
            | '\u{202a}'..='\u{202e}'
            | '\u{2066}'..='\u{2069}'
            | '\u{feff}'
    )
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
pub enum LogicalPathError {
    #[error("logical path exceeds the portable byte limit")]
    TooLong,
    #[error("logical paths must be root-relative")]
    Absolute,
    #[error("logical paths must not contain NUL")]
    Nul,
    #[error("host path separators are not valid in CoreFS paths")]
    HostSeparator,
    #[error("backend and URI path forms are not valid logical paths")]
    ForeignBackend,
    #[error("logical path contains an empty component")]
    EmptyComponent,
    #[error("logical path contains parent or current-directory traversal")]
    Traversal,
    #[error("logical path component exceeds the portable byte limit")]
    ComponentTooLong,
    #[error("logical path contains a control character")]
    ControlCharacter,
    #[error("logical path must use Unicode NFC")]
    NonCanonicalUnicode,
    #[error("logical path contains an ambiguous Unicode path character")]
    AmbiguousUnicode,
    #[error("logical path contains a reserved CoreFS component")]
    ReservedComponent,
}
