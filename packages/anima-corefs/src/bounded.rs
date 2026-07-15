//! Internal bounded-output helpers.

use std::io::{self, Write};

use serde::Serialize;

pub(crate) enum BoundedJsonError {
    LimitExceeded,
    Json(serde_json::Error),
}

pub(crate) fn json_to_vec<T: Serialize>(
    value: &T,
    limit: usize,
) -> Result<Vec<u8>, BoundedJsonError> {
    let mut writer = BoundedWriter::new(limit);
    let result = serde_json::to_writer(&mut writer, value);
    if writer.limit_exceeded() {
        return Err(BoundedJsonError::LimitExceeded);
    }
    result.map_err(BoundedJsonError::Json)?;
    Ok(writer.into_inner())
}

struct BoundedWriter {
    bytes: Vec<u8>,
    limit: usize,
    limit_exceeded: bool,
}

impl BoundedWriter {
    fn new(limit: usize) -> Self {
        Self {
            bytes: Vec::new(),
            limit,
            limit_exceeded: false,
        }
    }

    #[cfg(test)]
    fn len(&self) -> usize {
        self.bytes.len()
    }

    fn limit_exceeded(&self) -> bool {
        self.limit_exceeded
    }

    fn into_inner(self) -> Vec<u8> {
        self.bytes
    }
}

impl Write for BoundedWriter {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        let remaining = self.limit.saturating_sub(self.bytes.len());
        if buffer.len() > remaining {
            self.bytes.extend_from_slice(&buffer[..remaining]);
            self.limit_exceeded = true;
            if remaining == 0 {
                return Err(io::Error::other("bounded JSON output limit exceeded"));
            }
            return Ok(remaining);
        }
        self.bytes.extend_from_slice(buffer);
        Ok(buffer.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::BoundedWriter;

    #[test]
    fn bounded_writer_stops_json_serialization_without_growing_past_limit() {
        let mut writer = BoundedWriter::new(32);
        let value = "x".repeat(4_096);

        assert!(serde_json::to_writer(&mut writer, &value).is_err());
        assert!(writer.limit_exceeded());
        assert_eq!(writer.len(), 32);
    }
}
