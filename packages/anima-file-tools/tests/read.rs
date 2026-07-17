use std::io::Cursor;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Instant;

use anima_file_tools::{
    read_stream, BackendCapabilities, BackendKind, BackendPath, CancellationToken, FileBackend,
    FileToolError, MutationAtomicity, OperationControl, OperationLimits, PathSemantics,
    ReadBackend, ReadOptions,
};

#[derive(Clone)]
struct MemoryBackend {
    capabilities: BackendCapabilities,
    bytes: Vec<u8>,
}

impl MemoryBackend {
    fn host(bytes: Vec<u8>) -> Self {
        Self {
            capabilities: BackendCapabilities::new(
                BackendKind::HostFs,
                PathSemantics::HostNative,
                MutationAtomicity::BestEffort,
            ),
            bytes,
        }
    }
}

impl FileBackend for MemoryBackend {
    fn capabilities(&self) -> BackendCapabilities {
        self.capabilities
    }
}

impl ReadBackend for MemoryBackend {
    fn open_read(
        &self,
        _path: &str,
    ) -> Result<Box<dyn anima_file_tools::ReadSeek + Send>, FileToolError> {
        Ok(Box::new(Cursor::new(self.bytes.clone())))
    }
}

struct ControlledPositionBackend {
    called: Arc<AtomicBool>,
    cancellation: CancellationToken,
}

impl FileBackend for ControlledPositionBackend {
    fn capabilities(&self) -> BackendCapabilities {
        BackendCapabilities::new(
            BackendKind::CoreFs,
            PathSemantics::PortableNfcCaseSensitive,
            MutationAtomicity::CatalogGeneration,
        )
    }
}

impl ReadBackend for ControlledPositionBackend {
    fn open_read(
        &self,
        _path: &str,
    ) -> Result<Box<dyn anima_file_tools::ReadSeek + Send>, FileToolError> {
        Ok(Box::new(Cursor::new(vec![0_u8; 32])))
    }

    fn open_read_at(
        &self,
        _path: &str,
        offset: u64,
        max_bytes: usize,
        control: &OperationControl,
    ) -> Result<Box<dyn anima_file_tools::ReadSeek + Send>, FileToolError> {
        assert_eq!(offset, 17);
        assert_eq!(max_bytes, 1);
        self.called.store(true, Ordering::Release);
        self.cancellation.cancel();
        control.check()?;
        unreachable!("cancelled positioning must not produce a reader")
    }
}

#[test]
fn streams_large_reads_in_one_mibibyte_chunks_with_stable_offsets() {
    let bytes = vec![b'x'; 2 * 1024 * 1024 + 17];
    let backend = MemoryBackend::host(bytes.clone());
    let limits = OperationLimits::default().validate().unwrap();
    let options = ReadOptions {
        offset: 0,
        max_bytes: bytes.len(),
    };

    let chunks = read_stream(
        &backend,
        BackendPath::new(BackendKind::HostFs, "large.bin").unwrap(),
        options,
        limits,
        OperationControl::default(),
    )
    .unwrap()
    .collect::<Result<Vec<_>, _>>()
    .unwrap();

    assert_eq!(chunks.len(), 3);
    assert_eq!(chunks[0].offset, 0);
    assert_eq!(chunks[0].bytes.len(), 1024 * 1024);
    assert_eq!(chunks[1].offset, 1024 * 1024);
    assert_eq!(chunks[1].bytes.len(), 1024 * 1024);
    assert_eq!(chunks[2].offset, 2 * 1024 * 1024);
    assert_eq!(chunks[2].bytes.len(), 17);
}

#[test]
fn rejects_requests_larger_than_the_model_visible_response_cap() {
    let backend = MemoryBackend::host(Vec::new());
    let limits = OperationLimits::default().validate().unwrap();

    let error = read_stream(
        &backend,
        BackendPath::new(BackendKind::HostFs, "large.bin").unwrap(),
        ReadOptions {
            offset: 0,
            max_bytes: 4 * 1024 * 1024 + 1,
        },
        limits,
        OperationControl::default(),
    )
    .unwrap_err();

    assert_eq!(
        error,
        FileToolError::ResponseLimitExceeded {
            requested: 4 * 1024 * 1024 + 1,
            maximum: 4 * 1024 * 1024,
        }
    );
}

#[test]
fn rejects_cross_backend_paths_before_opening_content() {
    let backend = MemoryBackend::host(Vec::new());
    let limits = OperationLimits::default().validate().unwrap();

    let error = read_stream(
        &backend,
        BackendPath::new(BackendKind::CoreFs, "notes/today.md").unwrap(),
        ReadOptions {
            offset: 0,
            max_bytes: 1,
        },
        limits,
        OperationControl::default(),
    )
    .unwrap_err();

    assert_eq!(
        error,
        FileToolError::BackendMismatch {
            path_backend: BackendKind::CoreFs,
            selected_backend: BackendKind::HostFs,
        }
    );
}

#[test]
fn cancellation_is_checked_between_streamed_chunks() {
    let backend = MemoryBackend::host(vec![b'x'; 2 * 1024 * 1024]);
    let limits = OperationLimits::default().validate().unwrap();
    let cancellation = CancellationToken::new();
    let mut stream = read_stream(
        &backend,
        BackendPath::new(BackendKind::HostFs, "large.bin").unwrap(),
        ReadOptions {
            offset: 0,
            max_bytes: 2 * 1024 * 1024,
        },
        limits,
        OperationControl::new(cancellation.clone(), None),
    )
    .unwrap();

    assert_eq!(stream.next().unwrap().unwrap().bytes.len(), 1024 * 1024);
    cancellation.cancel();
    assert_eq!(stream.next().unwrap(), Err(FileToolError::Cancelled));
    assert!(stream.next().is_none());
}

#[test]
fn expired_deadline_fails_before_the_backend_is_opened() {
    let backend = MemoryBackend::host(Vec::new());
    let error = read_stream(
        &backend,
        BackendPath::new(BackendKind::HostFs, "notes.md").unwrap(),
        ReadOptions {
            offset: 0,
            max_bytes: 1,
        },
        OperationLimits::default().validate().unwrap(),
        OperationControl::new(CancellationToken::new(), Some(Instant::now())),
    )
    .unwrap_err();

    assert_eq!(error, FileToolError::DeadlineExceeded);
}

#[test]
fn nonzero_offset_positioning_is_backend_aware_and_receives_operation_control() {
    let cancellation = CancellationToken::new();
    let called = Arc::new(AtomicBool::new(false));
    let backend = ControlledPositionBackend {
        called: Arc::clone(&called),
        cancellation: cancellation.clone(),
    };

    let error = read_stream(
        &backend,
        BackendPath::new(BackendKind::CoreFs, "Notes/large.bin").unwrap(),
        ReadOptions {
            offset: 17,
            max_bytes: 1,
        },
        OperationLimits::default().validate().unwrap(),
        OperationControl::new(cancellation, None),
    )
    .unwrap_err();

    assert_eq!(error, FileToolError::Cancelled);
    assert!(called.load(Ordering::Acquire));
}
