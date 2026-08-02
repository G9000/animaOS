//! Canonical opaque identifiers for portable CoreFS objects and catalog entries.

pub const OPAQUE_ID_LENGTH: usize = 26;

#[derive(Clone, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
pub struct OpaqueId(String);

impl OpaqueId {
    pub fn generate() -> Result<Self, OpaqueIdError> {
        const CROCKFORD: &[u8; 32] = b"0123456789ABCDEFGHJKMNPQRSTVWXYZ";
        let mut random = [0_u8; 16];
        getrandom::getrandom(&mut random).map_err(|_| OpaqueIdError)?;
        let mut encoded = [0_u8; OPAQUE_ID_LENGTH];
        for (index, output) in encoded.iter_mut().enumerate() {
            let mut value = 0_u8;
            for offset in 0..5 {
                let bit = index * 5 + offset;
                value <<= 1;
                if bit >= 2 {
                    let source = bit - 2;
                    value |= (random[source / 8] >> (7 - source % 8)) & 1;
                }
            }
            *output = CROCKFORD[value as usize];
        }
        Self::parse(std::str::from_utf8(&encoded).map_err(|_| OpaqueIdError)?)
    }

    pub fn parse(value: &str) -> Result<Self, OpaqueIdError> {
        validate_opaque_id(value)?;
        Ok(Self(value.to_owned()))
    }

    /// Derives a stable, native-valid identifier for one legacy migration key.
    pub fn derive_migration(domain: &str, source_key: &[u8]) -> Result<Self, OpaqueIdError> {
        use sha2::Digest as _;

        if domain.is_empty() || source_key.is_empty() {
            return Err(OpaqueIdError);
        }
        let mut hasher = sha2::Sha256::new();
        hasher.update(b"anima-corefs-migration-opaque-id-v1\0");
        hasher.update((domain.len() as u64).to_be_bytes());
        hasher.update(domain.as_bytes());
        hasher.update((source_key.len() as u64).to_be_bytes());
        hasher.update(source_key);
        let digest = hasher.finalize();
        Self::from_128_bits(&digest[..16])
    }

    fn from_128_bits(input: &[u8]) -> Result<Self, OpaqueIdError> {
        const CROCKFORD: &[u8; 32] = b"0123456789ABCDEFGHJKMNPQRSTVWXYZ";
        if input.len() != 16 {
            return Err(OpaqueIdError);
        }
        let mut encoded = [0_u8; OPAQUE_ID_LENGTH];
        for (index, output) in encoded.iter_mut().enumerate() {
            let mut value = 0_u8;
            for offset in 0..5 {
                let bit = index * 5 + offset;
                value <<= 1;
                if bit >= 2 {
                    let source = bit - 2;
                    value |= (input[source / 8] >> (7 - source % 8)) & 1;
                }
            }
            *output = CROCKFORD[value as usize];
        }
        Self::parse(std::str::from_utf8(&encoded).map_err(|_| OpaqueIdError)?)
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
