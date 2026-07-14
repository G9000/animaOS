use std::io::{Cursor, Read, Seek, SeekFrom};
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc,
};

use anima_file_tools::{
    read_text_lines, BackendCapabilities, BackendKind, BackendPath, FileBackend, FileToolError,
    MutationAtomicity, OperationControl, OperationLimits, PathSemantics, ReadBackend, ReadSeek,
    TextReadIssue, TextReadRequest,
};

struct MemoryBackend(Vec<u8>);

struct CountingBackend {
    bytes: Vec<u8>,
    bytes_read: Arc<AtomicUsize>,
}

struct CountingReader {
    inner: Cursor<Vec<u8>>,
    bytes_read: Arc<AtomicUsize>,
}

impl Read for CountingReader {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        let read = self.inner.read(buffer)?;
        self.bytes_read.fetch_add(read, Ordering::Relaxed);
        Ok(read)
    }
}

impl Seek for CountingReader {
    fn seek(&mut self, position: SeekFrom) -> std::io::Result<u64> {
        self.inner.seek(position)
    }
}

impl FileBackend for MemoryBackend {
    fn capabilities(&self) -> BackendCapabilities {
        BackendCapabilities::new(
            BackendKind::HostFs,
            PathSemantics::HostNative,
            MutationAtomicity::BestEffort,
        )
    }
}

impl ReadBackend for MemoryBackend {
    fn open_read(&self, _path: &str) -> Result<Box<dyn ReadSeek + Send>, FileToolError> {
        Ok(Box::new(Cursor::new(self.0.clone())))
    }
}

impl FileBackend for CountingBackend {
    fn capabilities(&self) -> BackendCapabilities {
        BackendCapabilities::new(
            BackendKind::HostFs,
            PathSemantics::HostNative,
            MutationAtomicity::BestEffort,
        )
    }
}

impl ReadBackend for CountingBackend {
    fn open_read(&self, _path: &str) -> Result<Box<dyn ReadSeek + Send>, FileToolError> {
        Ok(Box::new(CountingReader {
            inner: Cursor::new(self.bytes.clone()),
            bytes_read: self.bytes_read.clone(),
        }))
    }
}

#[test]
fn reads_only_the_requested_line_window_with_stable_line_numbers() {
    let backend = MemoryBackend(b"zero\none\ntwo\nthree\n".to_vec());
    let page = read_text_lines(
        &backend,
        request(1, 2),
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap();

    assert_eq!(
        page.lines
            .iter()
            .map(|line| (line.number, line.text.as_str()))
            .collect::<Vec<_>>(),
        vec![(2, "one"), (3, "two")]
    );
    assert!(page.truncated);
    assert_eq!(page.next_line_offset, Some(3));
}

#[test]
fn rejects_binary_and_invalid_utf8_instead_of_returning_lossy_text() {
    for (bytes, issue) in [
        (b"text\0binary".to_vec(), TextReadIssue::BinaryContent),
        (vec![0xff, b'\n'], TextReadIssue::InvalidUtf8),
    ] {
        let error = read_text_lines(
            &MemoryBackend(bytes),
            request(0, 10),
            OperationLimits::default().validate().unwrap(),
            OperationControl::default(),
        )
        .unwrap_err();

        assert!(matches!(
            error,
            FileToolError::InvalidTextContent { reason, .. } if reason == issue
        ));
    }
}

#[test]
fn line_ceiling_is_enforced_without_allocating_the_entire_line() {
    let backend = MemoryBackend(vec![b'x'; 128]);
    let mut request = request(0, 1);
    request.max_line_bytes = 32;

    let error = read_text_lines(
        &backend,
        request,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap_err();

    assert!(matches!(
        error,
        FileToolError::InvalidTextContent {
            reason: TextReadIssue::LineTooLong,
            ..
        }
    ));
}

#[test]
fn line_ceiling_returns_before_streaming_the_rest_of_a_large_line() {
    let bytes = vec![b'x'; 1024 * 1024];
    let total_bytes = bytes.len();
    let bytes_read = Arc::new(AtomicUsize::new(0));
    let backend = CountingBackend {
        bytes,
        bytes_read: bytes_read.clone(),
    };
    let mut request = request(0, 1);
    request.max_line_bytes = 32;

    let error = read_text_lines(
        &backend,
        request,
        OperationLimits::default().validate().unwrap(),
        OperationControl::default(),
    )
    .unwrap_err();

    assert!(matches!(
        error,
        FileToolError::InvalidTextContent {
            reason: TextReadIssue::LineTooLong,
            ..
        }
    ));
    assert!(bytes_read.load(Ordering::Relaxed) < total_bytes);
}

fn request(offset_lines: usize, max_lines: usize) -> TextReadRequest {
    TextReadRequest {
        path: BackendPath::new(BackendKind::HostFs, "notes.md").unwrap(),
        offset_lines,
        max_lines,
        max_line_bytes: 64 * 1024,
    }
}
