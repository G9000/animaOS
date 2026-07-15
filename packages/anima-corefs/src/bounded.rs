//! Internal bounded-output helpers.

use std::io::{self, Write};

use serde::Serialize;

pub(crate) enum BoundedJsonError {
    LimitExceeded,
    Json(serde_json::Error),
}

pub(crate) fn clone_after_bounded_json_preflight<T: Clone + Serialize>(
    value: &T,
    limit: usize,
) -> Result<T, BoundedJsonError> {
    bounded_json_preflight(value, limit)?;
    Ok(value.clone())
}

pub(crate) fn bounded_json_preflight<T: Serialize>(
    value: &T,
    limit: usize,
) -> Result<(), BoundedJsonError> {
    let mut writer = CountingWriter::new(limit);
    let result = serde_json::to_writer(&mut writer, value);
    if writer.limit_exceeded() {
        return Err(BoundedJsonError::LimitExceeded);
    }
    result.map_err(BoundedJsonError::Json)?;
    Ok(())
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

struct CountingWriter {
    length: usize,
    limit: usize,
    limit_exceeded: bool,
}

impl CountingWriter {
    fn new(limit: usize) -> Self {
        Self {
            length: 0,
            limit,
            limit_exceeded: false,
        }
    }

    fn limit_exceeded(&self) -> bool {
        self.limit_exceeded
    }
}

impl Write for CountingWriter {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        let remaining = self.limit.saturating_sub(self.length);
        if buffer.len() > remaining {
            self.length = self.limit;
            self.limit_exceeded = true;
            if remaining == 0 {
                return Err(io::Error::other("bounded JSON output limit exceeded"));
            }
            return Ok(remaining);
        }
        self.length += buffer.len();
        Ok(buffer.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
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
    use std::sync::atomic::{AtomicUsize, Ordering};

    use serde::Serialize;

    use super::{clone_after_bounded_json_preflight, BoundedJsonError, BoundedWriter};

    #[derive(Serialize)]
    struct CloneProbe<'a> {
        payload: &'a str,
        #[serde(skip)]
        clone_count: &'a AtomicUsize,
    }

    impl Clone for CloneProbe<'_> {
        fn clone(&self) -> Self {
            self.clone_count.fetch_add(1, Ordering::SeqCst);
            Self {
                payload: self.payload,
                clone_count: self.clone_count,
            }
        }
    }

    #[test]
    fn bounded_writer_stops_json_serialization_without_growing_past_limit() {
        let mut writer = BoundedWriter::new(32);
        let value = "x".repeat(4_096);

        assert!(serde_json::to_writer(&mut writer, &value).is_err());
        assert!(writer.limit_exceeded());
        assert_eq!(writer.len(), 32);
    }

    #[test]
    fn oversized_json_is_rejected_before_clone() {
        let clone_count = AtomicUsize::new(0);
        let payload = "x".repeat(4_096);
        let value = CloneProbe {
            payload: &payload,
            clone_count: &clone_count,
        };

        assert!(matches!(
            clone_after_bounded_json_preflight(&value, 32),
            Err(BoundedJsonError::LimitExceeded)
        ));
        assert_eq!(clone_count.load(Ordering::SeqCst), 0);
    }
}
