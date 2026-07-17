// SPDX-License-Identifier: Apache-2.0
// Bounded chunk-streaming pattern adapted from OpenAI Codex codex-rs/file-system/src/lib.rs at
// 9e552e9d15ba52bed7077d5357f3e18e330f8f38; implementation rewritten for explicit backends.

use std::fmt;
use std::io::Read;

use crate::{BackendPath, FileToolError, OperationControl, ReadBackend, ReadSeek, ValidatedLimits};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ReadOptions {
    pub offset: u64,
    pub max_bytes: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ReadChunk {
    pub offset: u64,
    pub bytes: Vec<u8>,
}

pub struct ReadStream {
    reader: Box<dyn ReadSeek + Send>,
    control: OperationControl,
    chunk_bytes: usize,
    offset: u64,
    remaining: usize,
    finished: bool,
    path: String,
}

impl fmt::Debug for ReadStream {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ReadStream")
            .field("chunk_bytes", &self.chunk_bytes)
            .field("offset", &self.offset)
            .field("remaining", &self.remaining)
            .field("finished", &self.finished)
            .field("path", &self.path)
            .finish_non_exhaustive()
    }
}

pub fn read_stream<B: ReadBackend + ?Sized>(
    backend: &B,
    path: BackendPath,
    options: ReadOptions,
    limits: ValidatedLimits,
    control: OperationControl,
) -> Result<ReadStream, FileToolError> {
    control.check()?;
    let selected_backend = backend.capabilities().backend();
    if path.backend() != selected_backend {
        return Err(FileToolError::BackendMismatch {
            path_backend: path.backend(),
            selected_backend,
        });
    }
    if options.max_bytes > limits.response_bytes() {
        return Err(FileToolError::ResponseLimitExceeded {
            requested: options.max_bytes,
            maximum: limits.response_bytes(),
        });
    }

    let reader =
        backend.open_read_at(path.as_str(), options.offset, options.max_bytes, &control)?;

    Ok(ReadStream {
        reader,
        control,
        chunk_bytes: limits.read_chunk_bytes(),
        offset: options.offset,
        remaining: options.max_bytes,
        finished: false,
        path: path.as_str().to_string(),
    })
}

impl Iterator for ReadStream {
    type Item = Result<ReadChunk, FileToolError>;

    fn next(&mut self) -> Option<Self::Item> {
        self.next_with_max_bytes(usize::MAX)
    }
}

impl ReadStream {
    /// Pulls the next chunk without consuming more than `max_bytes` of body.
    /// This lets a result shaper reserve room for its own model-visible
    /// metadata without changing the shared payload ceiling.
    pub fn next_with_max_bytes(
        &mut self,
        max_bytes: usize,
    ) -> Option<Result<ReadChunk, FileToolError>> {
        if max_bytes == 0 {
            return None;
        }
        if self.finished || self.remaining == 0 {
            self.finished = true;
            return None;
        }
        if let Err(error) = self.control.check() {
            self.finished = true;
            return Some(Err(error));
        }

        let requested = self.chunk_bytes.min(self.remaining).min(max_bytes);
        let mut bytes = vec![0; requested];
        let read = match self.reader.read(&mut bytes) {
            Ok(0) => {
                self.finished = true;
                return None;
            }
            Ok(read) => read,
            Err(error) => {
                self.finished = true;
                return Some(Err(FileToolError::Backend {
                    operation: "read",
                    path: self.path.clone(),
                    message: error.to_string(),
                }));
            }
        };
        bytes.truncate(read);
        let offset = self.offset;
        self.offset = match self.offset.checked_add(read as u64) {
            Some(offset) => offset,
            None => {
                self.finished = true;
                return Some(Err(FileToolError::Backend {
                    operation: "read",
                    path: self.path.clone(),
                    message: "read offset overflowed u64".to_string(),
                }));
            }
        };
        self.remaining -= read;
        Some(Ok(ReadChunk { offset, bytes }))
    }
}
