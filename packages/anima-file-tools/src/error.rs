// SPDX-License-Identifier: Apache-2.0

use crate::BackendKind;
use thiserror::Error;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TextReadIssue {
    BinaryContent,
    InvalidUtf8,
    LineTooLong,
}

impl std::fmt::Display for TextReadIssue {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let label = match self {
            Self::BinaryContent => "binary content",
            Self::InvalidUtf8 => "invalid UTF-8",
            Self::LineTooLong => "line exceeds the configured byte limit",
        };
        formatter.write_str(label)
    }
}

#[derive(Clone, Debug, Eq, Error, PartialEq)]
pub enum FileToolError {
    #[error("invalid path {path}: {reason}")]
    InvalidPath { path: String, reason: String },
    #[error("path is for {path_backend:?}, but {selected_backend:?} was selected")]
    BackendMismatch {
        path_backend: BackendKind,
        selected_backend: BackendKind,
    },
    #[error("requested {requested} response bytes exceeds maximum {maximum}")]
    ResponseLimitExceeded { requested: usize, maximum: usize },
    #[error("{kind} response item requires {required} bytes; maximum is {maximum}")]
    ResponseItemTooLarge {
        kind: &'static str,
        required: usize,
        maximum: usize,
    },
    #[error("{operation} pagination cannot produce an advancing continuation cursor")]
    PaginationCannotAdvance { operation: &'static str },
    #[error("invalid {mode} pattern: {message}")]
    InvalidPattern { mode: &'static str, message: String },
    #[error("cannot read {path} as text: {reason}")]
    InvalidTextContent { path: String, reason: TextReadIssue },
    #[error("{operation} failed for {path}: {message}")]
    Backend {
        operation: &'static str,
        path: String,
        message: String,
    },
    #[error("operation cancelled")]
    Cancelled,
    #[error("operation deadline exceeded")]
    DeadlineExceeded,
}
