//! Canonical opaque identifiers for portable CoreFS objects and catalog entries.

pub const OPAQUE_ID_LENGTH: usize = 26;

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct OpaqueId(String);

impl OpaqueId {
    pub fn parse(value: &str) -> Result<Self, OpaqueIdError> {
        validate_opaque_id(value)?;
        Ok(Self(value.to_owned()))
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, thiserror::Error)]
#[error("opaque ID must be a canonical uppercase Crockford ULID")]
pub struct OpaqueIdError;

pub fn validate_opaque_id(value: &str) -> Result<(), OpaqueIdError> {
    let bytes = value.as_bytes();
    if bytes.len() != OPAQUE_ID_LENGTH || !matches!(bytes.first(), Some(b'0'..=b'7')) {
        return Err(OpaqueIdError);
    }
    if bytes.iter().copied().all(is_crockford_base32) {
        Ok(())
    } else {
        Err(OpaqueIdError)
    }
}

fn is_crockford_base32(value: u8) -> bool {
    matches!(
        value,
        b'0'..=b'9' | b'A'..=b'H' | b'J'..=b'K' | b'M'..=b'N' | b'P'..=b'T' | b'V'..=b'Z'
    )
}
