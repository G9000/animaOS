//! PyO3 bindings exposing anima-core to Python.
//!
//! Provides zero-overhead native imports for animaOS's Python layer.
//! Build with: `maturin develop --features python`

#[cfg(feature = "python")]
mod python {
    // PyO3 0.22 generated wrappers trigger this false positive for `PyResult` returns.
    #![allow(clippy::useless_conversion)]

    use base64::{engine::general_purpose::STANDARD as BASE64, Engine as _};
    use pyo3::buffer::PyBuffer;
    use pyo3::prelude::*;
    #[cfg(test)]
    use pyo3::types::PyByteArray;
    use pyo3::types::{PyBytes, PyDict, PyList, PyTuple};
    use pyo3::IntoPy;
    use serde::Deserialize;
    use serde_json::{json, Value};
    use std::collections::{BTreeMap, HashMap};
    use std::io::{self, Read, Write};
    use std::path::{Path, PathBuf};
    use std::sync::{Arc, Condvar, Mutex};

    use crate::cards::{
        CardStore, Cardinality, MemoryCard, MemoryKind, Polarity, SchemaRegistry, VersionRelation,
    };
    use crate::frame::{Frame, FrameKind, FrameSource, FrameStore};
    use crate::graph::{EntityKind, KnowledgeGraph};
    use crate::integrity::{
        scan_frame_store, verify_capsule_integrity, CapsuleIntegrityReport, CoreStats,
        IntegrityReport,
    };
    use crate::search::{HeatMeta, HeatParams};
    use crate::temporal::TemporalIndex;

    const CORE_FS_FFI_IN_MEMORY_LIMIT: usize = 16 * 1024 * 1024;

    struct PyBinaryReader<'py> {
        inner: Bound<'py, PyAny>,
    }

    impl<'py> PyBinaryReader<'py> {
        fn new(inner: &Bound<'py, PyAny>) -> Self {
            Self {
                inner: inner.clone(),
            }
        }
    }

    impl Read for PyBinaryReader<'_> {
        fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
            if buffer.is_empty() {
                return Ok(0);
            }
            let value = self
                .inner
                .call_method1("read", (buffer.len(),))
                .map_err(py_stream_io_error)?;
            let bytes = value.downcast::<PyBytes>().map_err(|error| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("read(size) must return bytes: {error}"),
                )
            })?;
            let bytes = bytes.as_bytes();
            if bytes.len() > buffer.len() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "read(size) returned more bytes than requested",
                ));
            }
            buffer[..bytes.len()].copy_from_slice(bytes);
            Ok(bytes.len())
        }
    }

    struct PyBinaryWriter<'py> {
        inner: Bound<'py, PyAny>,
        start_position: u64,
    }

    impl<'py> PyBinaryWriter<'py> {
        fn new(inner: &Bound<'py, PyAny>) -> PyResult<Self> {
            for method in ["write", "tell", "seek", "truncate"] {
                let value = inner
                    .getattr(method)
                    .map_err(|error| py_writer_protocol_error(method, error))?;
                if !value.is_callable() {
                    return Err(pyo3::exceptions::PyOSError::new_err(format!(
                        "CoreFS streaming writer {method} must be callable"
                    )));
                }
            }
            let start_position = inner
                .call_method0("tell")
                .and_then(|value| value.extract::<u64>())
                .map_err(|error| py_writer_protocol_error("tell", error))?;
            let end_position = inner
                .call_method1("seek", (0_i64, 2))
                .and_then(|value| value.extract::<u64>())
                .map_err(|error| py_writer_protocol_error("seek", error))?;
            inner
                .call_method1("seek", (start_position, 0))
                .map_err(|error| py_writer_protocol_error("seek", error))?;
            if start_position != end_position {
                return Err(pyo3::exceptions::PyOSError::new_err(
                    "CoreFS streaming writer must be positioned at end-of-file",
                ));
            }
            Ok(Self {
                inner: inner.clone(),
                start_position,
            })
        }

        fn rollback(&mut self) -> PyResult<()> {
            self.inner.call_method1("seek", (self.start_position, 0))?;
            self.inner
                .call_method1("truncate", (self.start_position,))?;
            self.inner.call_method1("seek", (self.start_position, 0))?;
            Ok(())
        }
    }

    impl Write for PyBinaryWriter<'_> {
        fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
            let value = self
                .inner
                .call_method1("write", (PyBytes::new_bound(self.inner.py(), buffer),))
                .map_err(py_stream_io_error)?;
            let written = value.extract::<usize>().map_err(py_stream_io_error)?;
            if written > buffer.len() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "write(bytes) reported more bytes than supplied",
                ));
            }
            Ok(written)
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    fn py_stream_io_error(error: PyErr) -> io::Error {
        io::Error::other(error.to_string())
    }

    fn py_writer_protocol_error(operation: &str, error: PyErr) -> PyErr {
        pyo3::exceptions::PyOSError::new_err(format!(
            "CoreFS streaming writer {operation} failed: {error}"
        ))
    }

    fn enforce_corefs_in_memory_limit(length: usize) -> PyResult<()> {
        if length > CORE_FS_FFI_IN_MEMORY_LIMIT {
            return Err(pyo3::exceptions::PyValueError::new_err(format!(
                "CoreFS in-memory binding is limited to {CORE_FS_FFI_IN_MEMORY_LIMIT} bytes; use a streaming CoreFS binding"
            )));
        }
        Ok(())
    }

    fn enforce_corefs_metadata_limit(length: usize) -> PyResult<()> {
        if length > anima_corefs::envelope::MAX_METADATA_PLAINTEXT_SIZE {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "CoreFS envelope limit exceeded: metadata plaintext",
            ));
        }
        Ok(())
    }

    fn enforce_corefs_catalog_plaintext_limit(length: usize) -> PyResult<()> {
        if length > anima_corefs::catalog::MAX_CATALOG_PLAINTEXT_SIZE {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "CoreFS catalog limit exceeded: catalog plaintext",
            ));
        }
        Ok(())
    }

    fn enforce_corefs_catalog_envelope_limit(length: usize) -> PyResult<()> {
        if length > anima_corefs::catalog::MAX_CATALOG_ENVELOPE_SIZE {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "CoreFS catalog limit exceeded: catalog envelope",
            ));
        }
        Ok(())
    }

    fn finish_corefs_stream<T>(
        writer: &mut PyBinaryWriter<'_>,
        result: Result<T, anima_corefs::envelope::EnvelopeError>,
    ) -> PyResult<T> {
        match result {
            Ok(value) => Ok(value),
            Err(error) => Err(rollback_corefs_stream(writer, corefs_envelope_error(error))),
        }
    }

    fn rollback_corefs_stream(writer: &mut PyBinaryWriter<'_>, original: PyErr) -> PyErr {
        if let Err(rollback) = writer.rollback() {
            pyo3::exceptions::PyOSError::new_err(format!(
                "CoreFS stream failed ({original}) and writer rollback failed: {rollback}"
            ))
        } else {
            original
        }
    }

    fn corefs_value_error(error: anima_corefs::crypto::CryptoError) -> PyErr {
        pyo3::exceptions::PyValueError::new_err(error.to_string())
    }

    fn corefs_envelope_error(error: anima_corefs::envelope::EnvelopeError) -> PyErr {
        match error {
            anima_corefs::envelope::EnvelopeError::Io(error) => {
                pyo3::exceptions::PyOSError::new_err(error.to_string())
            }
            error => pyo3::exceptions::PyValueError::new_err(error.to_string()),
        }
    }

    fn corefs_catalog_error(error: anima_corefs::catalog::CatalogError) -> PyErr {
        pyo3::exceptions::PyValueError::new_err(error.to_string())
    }

    fn corefs_commit_error(error: anima_corefs::transaction::CommitError) -> PyErr {
        match error {
            anima_corefs::transaction::CommitError::Io(error) => {
                pyo3::exceptions::PyOSError::new_err(error.to_string())
            }
            error => pyo3::exceptions::PyValueError::new_err(error.to_string()),
        }
    }

    fn corefs_validation_batch_error(
        error: anima_corefs::transaction::ValidationBatchError,
    ) -> PyErr {
        match error {
            anima_corefs::transaction::ValidationBatchError::Commit(
                anima_corefs::transaction::CommitError::Io(error),
            ) => pyo3::exceptions::PyOSError::new_err(error.to_string()),
            error => pyo3::exceptions::PyValueError::new_err(error.to_string()),
        }
    }

    pyo3::create_exception!(
        anima_core,
        CorefsPreparationConflictError,
        pyo3::exceptions::PyException
    );
    pyo3::create_exception!(
        anima_core,
        CorefsPreparationCorruptionError,
        pyo3::exceptions::PyException
    );
    pyo3::create_exception!(
        anima_core,
        CorefsPreparationSourceFenceError,
        pyo3::exceptions::PyException
    );

    fn corefs_preparation_error(
        error: anima_corefs::transaction::PreparationSessionError,
    ) -> PyErr {
        use anima_corefs::transaction::PreparationSessionError;

        match error {
            PreparationSessionError::Invalid(message)
            | PreparationSessionError::Missing(message) => {
                pyo3::exceptions::PyValueError::new_err(message)
            }
            PreparationSessionError::Corruption(message) => {
                CorefsPreparationCorruptionError::new_err(message)
            }
            PreparationSessionError::Conflict(message) => {
                CorefsPreparationConflictError::new_err(message)
            }
            PreparationSessionError::SourceFence(message) => {
                CorefsPreparationSourceFenceError::new_err(message)
            }
            PreparationSessionError::Io(error) => {
                pyo3::exceptions::PyOSError::new_err(error.to_string())
            }
        }
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct PreparationBeginWire {
        scope: String,
        expected_validation_generation: Option<u64>,
        expected_validation_catalog_sha256: Option<String>,
        source_owner_id: String,
        source_schema_version: u16,
        source_mutation_generation: u64,
        source_inventory_sha256: String,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct PreparationCasWire {
        pointer_sha256: String,
        snapshot_sequence: u64,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct PreparationIdentityWire {
        object_id: String,
        revision: u64,
        content_sha256: String,
        preparation_ordinal: u64,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct PreparationObjectWire {
        object_id: String,
        revision: u64,
        object_key_epoch: u32,
        kind: String,
        parent_id: String,
        name: String,
        content_type: String,
        body_encoding: String,
        body_length: u64,
        content_sha256: String,
        created_at: String,
        updated_at: String,
        source_character_count: Option<usize>,
        #[serde(default)]
        references: Vec<String>,
        policy: String,
        stable_role: Option<String>,
        #[serde(default)]
        graph_metadata: BTreeMap<String, Value>,
        source_fingerprint_sha256: String,
        converter_format_version: u16,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct PreparationPrepareObjectWire {
        expected: PreparationCasWire,
        object: PreparationObjectWire,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct PreparationReconciliationWire {
        cursor_position: Option<u64>,
        max_items: u32,
        max_bytes: u32,
        #[serde(default)]
        expected: Vec<PreparationIdentityWire>,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct PreparationSealWire {
        expected: PreparationCasWire,
        source_mutation_generation: u64,
        source_inventory_sha256: String,
        folders: Vec<ValidationBatchFolderWire>,
        objects: Vec<PreparationIdentityWire>,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct PreparationFinalizeWire {
        preparation_id: String,
        expected: PreparationCasWire,
        source_mutation_generation: u64,
        source_inventory_sha256: String,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct PreparationAbandonWire {
        preparation_id: String,
        expected: PreparationCasWire,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct PreparationQuarantineWire {
        expected_pointer_sha256: String,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct ValidationBatchWire {
        #[serde(default)]
        initialize: bool,
        expected_generation: Option<u64>,
        expected_catalog_hash: Option<String>,
        folders: Vec<ValidationBatchFolderWire>,
        objects: Vec<ValidationBatchObjectWire>,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct ValidationBatchFolderWire {
        stable_id: String,
        parent_id: Option<String>,
        name: String,
        role: Option<String>,
        policy: String,
        #[serde(default)]
        metadata: BTreeMap<String, Value>,
    }

    #[derive(Deserialize)]
    #[serde(rename_all = "camelCase", deny_unknown_fields)]
    struct ValidationBatchObjectWire {
        stable_id: String,
        parent_id: String,
        name: String,
        kind: String,
        content_type: String,
        body_encoding: String,
        content_base64: Option<String>,
        content_index: Option<usize>,
        created_at: String,
        updated_at: String,
        source_character_count: Option<usize>,
        expected_revision: Option<u64>,
        #[serde(default)]
        references: Vec<String>,
        policy: String,
        #[serde(default)]
        metadata: BTreeMap<String, Value>,
    }

    fn validation_batch_policy(
        value: &str,
    ) -> PyResult<anima_corefs::transaction::ValidationBatchPolicy> {
        match value {
            "user-write" => Ok(anima_corefs::transaction::ValidationBatchPolicy::UserWrite),
            "inherit" => Ok(anima_corefs::transaction::ValidationBatchPolicy::Inherit),
            "deny" => Ok(anima_corefs::transaction::ValidationBatchPolicy::Deny),
            _ => Err(pyo3::exceptions::PyValueError::new_err(
                "validation batch policy must be user-write, inherit, or deny",
            )),
        }
    }

    fn decode_preparation_json<T: for<'de> Deserialize<'de>>(encoded: &str) -> PyResult<T> {
        enforce_corefs_catalog_plaintext_limit(encoded.len())?;
        serde_json::from_str(encoded)
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))
    }

    fn preparation_cas_wire(
        value: PreparationCasWire,
    ) -> anima_corefs::transaction::PreparationCasV1 {
        anima_corefs::transaction::PreparationCasV1 {
            pointer_sha256: value.pointer_sha256,
            snapshot_sequence: value.snapshot_sequence,
        }
    }

    fn preparation_identity_wire(
        value: PreparationIdentityWire,
    ) -> anima_corefs::transaction::PreparationIdentityV1 {
        anima_corefs::transaction::PreparationIdentityV1 {
            object_id: value.object_id,
            revision: value.revision,
            content_sha256: value.content_sha256,
            preparation_ordinal: value.preparation_ordinal,
        }
    }

    fn preparation_folder_wire(
        value: ValidationBatchFolderWire,
    ) -> PyResult<anima_corefs::transaction::ValidationBatchFolder> {
        Ok(anima_corefs::transaction::ValidationBatchFolder {
            stable_id: value.stable_id,
            parent_id: value.parent_id,
            name: value.name,
            role: value.role,
            policy: validation_batch_policy(&value.policy)?,
            metadata: value.metadata,
        })
    }

    fn preparation_object_wire(
        value: PreparationObjectWire,
    ) -> PyResult<anima_corefs::transaction::PreparationObjectV1> {
        let kind =
            anima_corefs::crypto::ObjectKind::parse(&value.kind).map_err(corefs_value_error)?;
        let body_encoding = match value.body_encoding.as_str() {
            "utf-8" => anima_corefs::envelope::BodyEncoding::Utf8,
            "binary" => anima_corefs::envelope::BodyEncoding::Binary,
            _ => {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "preparation object bodyEncoding must be utf-8 or binary",
                ))
            }
        };
        Ok(anima_corefs::transaction::PreparationObjectV1 {
            object_id: value.object_id,
            revision: value.revision,
            object_key_epoch: value.object_key_epoch,
            kind,
            parent_id: value.parent_id,
            name: value.name,
            content_type: value.content_type,
            body_encoding,
            body_length: value.body_length,
            content_sha256: value.content_sha256,
            created_at: value.created_at,
            updated_at: value.updated_at,
            source_character_count: value.source_character_count,
            references: value.references,
            policy: validation_batch_policy(&value.policy)?,
            stable_role: value.stable_role,
            graph_metadata: value.graph_metadata,
            source_fingerprint_sha256: value.source_fingerprint_sha256,
            converter_format_version: value.converter_format_version,
        })
    }

    fn preparation_to_py(py: Python<'_>, value: impl serde::Serialize) -> PyResult<PyObject> {
        let value = serde_json::to_value(value)
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        json_value_to_py(py, value)
    }

    fn decode_validation_batch_json(
        batch_json: &str,
    ) -> PyResult<anima_corefs::transaction::ValidationBatch> {
        decode_validation_batch_parts_json(batch_json, None)
    }

    fn decode_validation_batch_parts_json(
        batch_json: &str,
        content_parts: Option<Vec<Vec<u8>>>,
    ) -> PyResult<anima_corefs::transaction::ValidationBatch> {
        enforce_corefs_catalog_plaintext_limit(batch_json.len())?;
        let wire: ValidationBatchWire = serde_json::from_str(batch_json)
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        let mode =
            match (
                wire.initialize,
                wire.expected_generation,
                wire.expected_catalog_hash,
            ) {
                (true, None, None) => anima_corefs::transaction::ValidationBatchMode::Initialize,
                (false, Some(generation), Some(catalog_hash)) => {
                    anima_corefs::transaction::ValidationBatchMode::Expect {
                        generation,
                        catalog_hash,
                    }
                }
                _ => return Err(pyo3::exceptions::PyValueError::new_err(
                    "validation batch requires explicit initialization or an exact expected head",
                )),
            };
        let folders = wire
            .folders
            .into_iter()
            .map(|folder| {
                Ok(anima_corefs::transaction::ValidationBatchFolder {
                    stable_id: folder.stable_id,
                    parent_id: folder.parent_id,
                    name: folder.name,
                    role: folder.role,
                    policy: validation_batch_policy(&folder.policy)?,
                    metadata: folder.metadata,
                })
            })
            .collect::<PyResult<Vec<_>>>()?;
        let object_count = wire.objects.len();
        let part_count = content_parts.as_ref().map_or(0, Vec::len);
        if content_parts.is_some() && object_count != part_count {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "validation content parts must have one ordered part per object",
            ));
        }
        let objects =
            wire.objects
                .into_iter()
                .enumerate()
                .map(|(object_index, object)| {
                    let kind = anima_corefs::crypto::ObjectKind::parse(&object.kind)
                        .map_err(corefs_value_error)?;
                    let body_encoding = match object.body_encoding.as_str() {
                        "utf-8" => anima_corefs::envelope::BodyEncoding::Utf8,
                        "binary" => anima_corefs::envelope::BodyEncoding::Binary,
                        _ => {
                            return Err(pyo3::exceptions::PyValueError::new_err(
                                "validation object bodyEncoding must be utf-8 or binary",
                            ))
                        }
                    };
                    let content =
                        match (object.content_base64, object.content_index) {
                            (Some(encoded), None) if content_parts.is_none() => {
                                BASE64.decode(encoded).map_err(|_| {
                                    pyo3::exceptions::PyValueError::new_err(
                                        "validation object contentBase64 is invalid",
                                    )
                                })?
                            }
                            (None, Some(index)) if index == object_index => content_parts
                                .as_ref()
                                .and_then(|parts| parts.get(index))
                                .cloned()
                                .ok_or_else(|| {
                                    pyo3::exceptions::PyValueError::new_err(
                                        "validation object contentIndex is out of range",
                                    )
                                })?,
                            _ => return Err(pyo3::exceptions::PyValueError::new_err(
                                "validation object requires exactly one matching content transport",
                            )),
                        };
                    Ok(anima_corefs::transaction::ValidationBatchObject {
                        stable_id: object.stable_id,
                        parent_id: object.parent_id,
                        name: object.name,
                        kind,
                        content_type: object.content_type,
                        body_encoding,
                        content,
                        created_at: object.created_at,
                        updated_at: object.updated_at,
                        source_character_count: object.source_character_count,
                        expected_revision: object.expected_revision,
                        references: object.references,
                        policy: validation_batch_policy(&object.policy)?,
                        metadata: object.metadata,
                    })
                })
                .collect::<PyResult<Vec<_>>>()?;
        Ok(anima_corefs::transaction::ValidationBatch {
            mode,
            folders,
            objects,
        })
    }

    fn corefs_logical_error(error: anima_corefs::logical::LogicalError) -> PyErr {
        pyo3::exceptions::PyValueError::new_err(error.to_string())
    }

    fn corefs_validated_limits(
        read_chunk_bytes: Option<usize>,
        walk_depth: Option<usize>,
        walk_directories: Option<usize>,
        walk_entries: Option<usize>,
        response_bytes: Option<usize>,
    ) -> PyResult<anima_file_tools::ValidatedLimits> {
        let defaults = anima_file_tools::OperationLimits::default();
        anima_file_tools::OperationLimits {
            read_chunk_bytes: read_chunk_bytes.unwrap_or(defaults.read_chunk_bytes),
            walk_depth: walk_depth.unwrap_or(defaults.walk_depth),
            walk_directories: walk_directories.unwrap_or(defaults.walk_directories),
            walk_entries: walk_entries.unwrap_or(defaults.walk_entries),
            response_bytes: response_bytes.unwrap_or(defaults.response_bytes),
        }
        .validate()
        .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))
    }

    fn corefs_open_read_snapshot_with_coordinator(
        coordinator: &anima_corefs::transaction::CoreCommitCoordinator,
        keys: &PyCorefsSubkeys,
        selected_generation: u64,
        selected_catalog_hash: &str,
    ) -> PyResult<anima_corefs::logical::CoreFsReadSnapshot> {
        let selected = coordinator
            .load_validation_snapshot(&keys.inner)
            .map_err(corefs_commit_error)?
            .ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err("CoreFS validation snapshot is missing")
            })?;
        let head = selected.head();
        if head.generation() != selected_generation || head.catalog_hash() != selected_catalog_hash
        {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "CoreFS validation snapshot no longer matches selected generation/catalog hash",
            ));
        }
        let keyring = anima_corefs::rotation::FrkKeyring::new([&keys.inner])
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        anima_corefs::logical::CoreFsReadSnapshot::open(coordinator, &selected, &keyring)
            .map_err(corefs_logical_error)
    }

    fn corefs_open_read_snapshot(
        core_root: &str,
        core_id: &str,
        keys: &PyCorefsSubkeys,
        selected_generation: u64,
        selected_catalog_hash: &str,
    ) -> PyResult<anima_corefs::logical::CoreFsReadSnapshot> {
        let coordinator = anima_corefs::transaction::CoreCommitCoordinator::new(core_root, core_id)
            .map_err(corefs_commit_error)?;
        corefs_open_read_snapshot_with_coordinator(
            &coordinator,
            keys,
            selected_generation,
            selected_catalog_hash,
        )
    }

    fn corefs_wire_to_py(
        py: Python<'_>,
        result: impl anima_corefs::logical::ModelWireV1,
    ) -> PyResult<PyObject> {
        let wire = result.to_model_wire_v1().map_err(corefs_logical_error)?;
        Ok(PyBytes::new_bound(py, &wire).into_py(py))
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    enum CorefsSessionPhase {
        Open,
        Releasing,
        Closing,
        Closed,
    }

    #[derive(Debug)]
    struct CorefsSessionState {
        phase: CorefsSessionPhase,
        active_operations: usize,
        terminal_close: bool,
        teardown_owned: bool,
    }

    impl Default for CorefsSessionState {
        fn default() -> Self {
            Self {
                phase: CorefsSessionPhase::Open,
                active_operations: 0,
                terminal_close: false,
                teardown_owned: false,
            }
        }
    }

    #[derive(Debug, Default)]
    struct CorefsSessionLifecycle {
        state: Mutex<CorefsSessionState>,
        changed: Condvar,
    }

    #[derive(Debug)]
    struct CorefsOperationGuard {
        lifecycle: Arc<CorefsSessionLifecycle>,
    }

    impl Drop for CorefsOperationGuard {
        fn drop(&mut self) {
            let mut state = self
                .lifecycle
                .state
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            debug_assert!(state.active_operations > 0);
            state.active_operations = state.active_operations.saturating_sub(1);
            self.lifecycle.changed.notify_all();
        }
    }

    #[pyclass(name = "CorefsSession")]
    struct PyCorefsSession {
        canonical_root: PathBuf,
        core_id: String,
        coordinator: Arc<anima_corefs::transaction::CoreCommitCoordinator>,
        lifecycle: Arc<CorefsSessionLifecycle>,
    }

    impl PyCorefsSession {
        fn acquire_operation(&self) -> PyResult<CorefsOperationGuard> {
            let mut state = self
                .lifecycle
                .state
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            if state.phase != CorefsSessionPhase::Open || state.terminal_close {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(format!(
                    "CoreFS session is {}",
                    match state.phase {
                        CorefsSessionPhase::Open => "not accepting operations",
                        CorefsSessionPhase::Releasing => "releasing its object lease",
                        CorefsSessionPhase::Closing => "closing",
                        CorefsSessionPhase::Closed => "closed",
                    }
                )));
            }
            state.active_operations = state.active_operations.checked_add(1).ok_or_else(|| {
                pyo3::exceptions::PyRuntimeError::new_err("CoreFS session operation count overflow")
            })?;
            Ok(CorefsOperationGuard {
                lifecycle: Arc::clone(&self.lifecycle),
            })
        }

        fn begin_lease_release(&self) -> PyResult<()> {
            self.coordinator
                .begin_object_lease_release()
                .map_err(corefs_commit_error)
        }

        fn finish_lease_release(&self, clear_cached_state: bool) -> PyResult<()> {
            self.coordinator
                .finish_object_lease_release()
                .map_err(corefs_commit_error)?;
            if clear_cached_state {
                self.coordinator.clear_cached_state();
            }
            Ok(())
        }

        fn wait_for_active_operations(&self) {
            let state = self
                .lifecycle
                .state
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            drop(
                self.lifecycle
                    .changed
                    .wait_while(state, |state| state.active_operations != 0)
                    .unwrap_or_else(|poisoned| poisoned.into_inner()),
            );
        }

        fn begin_close_native(&self) {
            let mut state = self
                .lifecycle
                .state
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            state.terminal_close = true;
            self.lifecycle.changed.notify_all();
        }

        fn release_native(&self) -> PyResult<()> {
            self.coordinator
                .ensure_object_lease_release_not_reentrant()
                .map_err(corefs_commit_error)?;
            {
                let mut state = self
                    .lifecycle
                    .state
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                match state.phase {
                    CorefsSessionPhase::Open if !state.terminal_close => {
                        state.phase = CorefsSessionPhase::Releasing;
                        state.teardown_owned = true;
                        self.lifecycle.changed.notify_all();
                    }
                    CorefsSessionPhase::Releasing => {
                        drop(
                            self.lifecycle
                                .changed
                                .wait_while(state, |state| {
                                    matches!(
                                        state.phase,
                                        CorefsSessionPhase::Releasing | CorefsSessionPhase::Closing
                                    )
                                })
                                .unwrap_or_else(|poisoned| poisoned.into_inner()),
                        );
                        return Ok(());
                    }
                    CorefsSessionPhase::Closing => {
                        return Err(pyo3::exceptions::PyRuntimeError::new_err(
                            "CoreFS session is closing",
                        ));
                    }
                    CorefsSessionPhase::Closed => {
                        return Err(pyo3::exceptions::PyRuntimeError::new_err(
                            "CoreFS session is closed",
                        ));
                    }
                    CorefsSessionPhase::Open => {
                        return Err(pyo3::exceptions::PyRuntimeError::new_err(
                            "CoreFS session is closing",
                        ));
                    }
                }
            }

            self.begin_lease_release()?;
            self.wait_for_active_operations();
            self.finish_lease_release(false)?;

            let terminal_close = {
                let mut state = self
                    .lifecycle
                    .state
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                debug_assert!(state.teardown_owned);
                if state.terminal_close {
                    true
                } else {
                    state.teardown_owned = false;
                    state.phase = CorefsSessionPhase::Open;
                    self.coordinator.resume_object_lease_publication();
                    self.lifecycle.changed.notify_all();
                    false
                }
            };
            if terminal_close {
                self.coordinator.clear_cached_state();
                let mut state = self
                    .lifecycle
                    .state
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                debug_assert!(state.teardown_owned);
                state.teardown_owned = false;
                state.phase = CorefsSessionPhase::Closed;
                self.lifecycle.changed.notify_all();
            }
            Ok(())
        }

        fn close_native(&self) -> PyResult<()> {
            self.coordinator
                .ensure_object_lease_release_not_reentrant()
                .map_err(corefs_commit_error)?;
            let owns_teardown = {
                let mut state = self
                    .lifecycle
                    .state
                    .lock()
                    .unwrap_or_else(|poisoned| poisoned.into_inner());
                state.terminal_close = true;
                match state.phase {
                    CorefsSessionPhase::Open => {
                        state.phase = CorefsSessionPhase::Closing;
                        state.teardown_owned = true;
                        self.lifecycle.changed.notify_all();
                        true
                    }
                    CorefsSessionPhase::Releasing => {
                        state.phase = CorefsSessionPhase::Closing;
                        self.lifecycle.changed.notify_all();
                        drop(
                            self.lifecycle
                                .changed
                                .wait_while(state, |state| {
                                    state.phase != CorefsSessionPhase::Closed
                                })
                                .unwrap_or_else(|poisoned| poisoned.into_inner()),
                        );
                        false
                    }
                    CorefsSessionPhase::Closing => {
                        drop(
                            self.lifecycle
                                .changed
                                .wait_while(state, |state| {
                                    state.phase != CorefsSessionPhase::Closed
                                })
                                .unwrap_or_else(|poisoned| poisoned.into_inner()),
                        );
                        false
                    }
                    CorefsSessionPhase::Closed => false,
                }
            };
            if !owns_teardown {
                return Ok(());
            }

            self.begin_lease_release()?;
            self.wait_for_active_operations();
            self.finish_lease_release(true)?;

            let mut state = self
                .lifecycle
                .state
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            debug_assert!(state.teardown_owned);
            state.teardown_owned = false;
            state.phase = CorefsSessionPhase::Closed;
            self.lifecycle.changed.notify_all();
            Ok(())
        }

        fn open_read_snapshot(
            &self,
            keys: &PyCorefsSubkeys,
            selected_generation: u64,
            selected_catalog_hash: &str,
        ) -> PyResult<anima_corefs::logical::CoreFsReadSnapshot> {
            corefs_open_read_snapshot_with_coordinator(
                self.coordinator.as_ref(),
                keys,
                selected_generation,
                selected_catalog_hash,
            )
        }

        #[cfg(test)]
        fn coordinator_for_test(&self) -> Arc<anima_corefs::transaction::CoreCommitCoordinator> {
            Arc::clone(&self.coordinator)
        }

        #[cfg(test)]
        fn acquire_operation_for_test(&self) -> PyResult<CorefsOperationGuard> {
            self.acquire_operation()
        }

        #[cfg(test)]
        fn phase_for_test(&self) -> CorefsSessionPhase {
            self.lifecycle
                .state
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .phase
        }

        #[cfg(test)]
        fn active_operations_for_test(&self) -> usize {
            self.lifecycle
                .state
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .active_operations
        }

        #[cfg(test)]
        fn wait_for_phase_for_test(&self, expected: CorefsSessionPhase) {
            let state = self
                .lifecycle
                .state
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner());
            drop(
                self.lifecycle
                    .changed
                    .wait_while(state, |state| state.phase != expected)
                    .unwrap_or_else(|poisoned| poisoned.into_inner()),
            );
        }
    }

    impl Drop for PyCorefsSession {
        fn drop(&mut self) {
            // SAFETY: `Py_IsInitialized` has no preconditions and does not require
            // the GIL. A Python-owned instance takes the allow-threads path; pure
            // Rust tests and interpreter shutdown can drain natively without trying
            // to initialize or re-enter Python.
            if unsafe { pyo3::ffi::Py_IsInitialized() } != 0 {
                Python::with_gil(|py| {
                    let _ = py.allow_threads(|| self.close_native());
                });
            } else {
                let _ = self.close_native();
            }
        }
    }

    #[allow(clippy::too_many_arguments)]
    #[pymethods]
    impl PyCorefsSession {
        #[new]
        fn new(core_root: &str, core_id: &str) -> PyResult<Self> {
            let coordinator =
                anima_corefs::transaction::CoreCommitCoordinator::new(core_root, core_id)
                    .map_err(corefs_commit_error)?;
            let canonical_root = coordinator.core_root().to_path_buf();
            Ok(Self {
                canonical_root,
                core_id: core_id.to_owned(),
                coordinator: Arc::new(coordinator),
                lifecycle: Arc::new(CorefsSessionLifecycle::default()),
            })
        }

        #[getter]
        fn core_root(&self) -> String {
            self.canonical_root.to_string_lossy().into_owned()
        }

        #[getter]
        fn core_id(&self) -> &str {
            &self.core_id
        }

        fn release_object_lease(&self, py: Python<'_>) -> PyResult<()> {
            py.allow_threads(|| self.release_native())
        }

        fn close(&self, py: Python<'_>) -> PyResult<()> {
            py.allow_threads(|| self.close_native())
        }

        fn begin_close(&self) {
            self.begin_close_native();
        }

        fn preparation_begin_or_resume_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            request_json: &str,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let wire: PreparationBeginWire = decode_preparation_json(request_json)?;
            let request = anima_corefs::transaction::PreparationBeginV1 {
                scope: wire.scope,
                expected_validation_generation: wire.expected_validation_generation,
                expected_validation_catalog_sha256: wire.expected_validation_catalog_sha256,
                source_owner_id: wire.source_owner_id,
                source_schema_version: wire.source_schema_version,
                source_mutation_generation: wire.source_mutation_generation,
                source_inventory_sha256: wire.source_inventory_sha256,
            };
            let status = py
                .allow_threads(|| {
                    self.coordinator
                        .preparation_begin_or_resume_v1(&keys.inner, &request)
                })
                .map_err(corefs_preparation_error)?;
            preparation_to_py(py, status)
        }

        #[pyo3(signature = (keys, reconciliation_json = None))]
        fn preparation_status_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            reconciliation_json: Option<&str>,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let status = py
                .allow_threads(|| self.coordinator.preparation_status_v1(&keys.inner))
                .map_err(corefs_preparation_error)?;
            let mut value = serde_json::to_value(status)
                .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
            if let Some(encoded) = reconciliation_json {
                let wire: PreparationReconciliationWire = decode_preparation_json(encoded)?;
                let request = anima_corefs::transaction::PreparationReconciliationV1 {
                    cursor_position: wire.cursor_position,
                    max_items: wire.max_items,
                    max_bytes: wire.max_bytes,
                    expected: wire
                        .expected
                        .into_iter()
                        .map(preparation_identity_wire)
                        .collect(),
                };
                let page = py
                    .allow_threads(|| {
                        self.coordinator
                            .preparation_reconciliation_v1(&keys.inner, &request)
                    })
                    .map_err(corefs_preparation_error)?;
                value
                    .as_object_mut()
                    .expect("preparation status serializes as an object")
                    .insert(
                        "reconciliation".to_owned(),
                        serde_json::to_value(page).map_err(|error| {
                            pyo3::exceptions::PyValueError::new_err(error.to_string())
                        })?,
                    );
            }
            json_value_to_py(py, value)
        }

        fn preparation_prepare_object_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            request_json: &str,
            body: PyBuffer<u8>,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let wire: PreparationPrepareObjectWire = decode_preparation_json(request_json)?;
            let expected = preparation_cas_wire(wire.expected);
            let request = preparation_object_wire(wire.object)?;
            let body = body.to_vec(py)?;
            let outcome = py
                .allow_threads(|| {
                    let mut reader = io::Cursor::new(body.as_slice());
                    self.coordinator.preparation_prepare_object_v1(
                        &keys.inner,
                        &expected,
                        &request,
                        &mut reader,
                    )
                })
                .map_err(corefs_preparation_error)?;
            preparation_to_py(py, outcome)
        }

        fn preparation_seal_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            request_json: &str,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let wire: PreparationSealWire = decode_preparation_json(request_json)?;
            let expected = preparation_cas_wire(wire.expected);
            let request = anima_corefs::transaction::PreparationSealV1 {
                source_mutation_generation: wire.source_mutation_generation,
                source_inventory_sha256: wire.source_inventory_sha256,
                folders: wire
                    .folders
                    .into_iter()
                    .map(preparation_folder_wire)
                    .collect::<PyResult<Vec<_>>>()?,
                objects: wire
                    .objects
                    .into_iter()
                    .map(preparation_identity_wire)
                    .collect(),
            };
            let status = py
                .allow_threads(|| {
                    self.coordinator
                        .preparation_seal_v1(&keys.inner, &expected, &request)
                })
                .map_err(corefs_preparation_error)?;
            preparation_to_py(py, status)
        }

        fn preparation_finalize_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            request_json: &str,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let wire: PreparationFinalizeWire = decode_preparation_json(request_json)?;
            let request = anima_corefs::transaction::PreparationFinalizeV1 {
                preparation_id: wire.preparation_id,
                expected: preparation_cas_wire(wire.expected),
                source_mutation_generation: wire.source_mutation_generation,
                source_inventory_sha256: wire.source_inventory_sha256,
            };
            let receipt = py
                .allow_threads(|| {
                    self.coordinator
                        .preparation_finalize_v1(&keys.inner, &request)
                })
                .map_err(corefs_preparation_error)?;
            preparation_to_py(py, receipt)
        }

        fn preparation_abandon_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            request_json: &str,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let wire: PreparationAbandonWire = decode_preparation_json(request_json)?;
            let request = anima_corefs::transaction::PreparationAbandonV1 {
                preparation_id: wire.preparation_id,
                expected: preparation_cas_wire(wire.expected),
            };
            let receipt = py
                .allow_threads(|| {
                    self.coordinator
                        .preparation_abandon_v1(&keys.inner, &request)
                })
                .map_err(corefs_preparation_error)?;
            preparation_to_py(py, receipt)
        }

        fn preparation_quarantine_corrupt_pointer_v1(
            &self,
            py: Python<'_>,
            retained_keys: Vec<PyRef<'_, PyCorefsSubkeys>>,
            active_keys: &PyCorefsSubkeys,
            request_json: &str,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let wire: PreparationQuarantineWire = decode_preparation_json(request_json)?;
            let request = anima_corefs::transaction::PreparationQuarantineV1 {
                expected_pointer_sha256: wire.expected_pointer_sha256,
            };
            let keyring = anima_corefs::rotation::FrkKeyring::new(
                retained_keys.iter().map(|keys| &keys.inner),
            )
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
            let receipt = py
                .allow_threads(|| {
                    self.coordinator.preparation_quarantine_corrupt_pointer_v1(
                        &keyring,
                        &active_keys.inner,
                        &request,
                    )
                })
                .map_err(corefs_preparation_error)?;
            preparation_to_py(py, receipt)
        }

        fn validation_snapshot(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let selected = self
                .coordinator
                .load_validation_snapshot(&keys.inner)
                .map_err(corefs_commit_error)?
                .ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err("CoreFS validation snapshot is missing")
                })?;
            let head = selected.head();
            json_value_to_py(
                py,
                json!({
                    "generation": head.generation(),
                    "catalogHash": head.catalog_hash(),
                }),
            )
        }

        fn validation_batch_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            batch_json: &str,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let batch = decode_validation_batch_json(batch_json)?;
            let outcome = py
                .allow_threads(|| self.coordinator.apply_validation_batch(&keys.inner, batch))
                .map_err(corefs_validation_batch_error)?;
            let head = outcome.snapshot().head();
            json_value_to_py(
                py,
                json!({
                    "generation": head.generation(),
                    "catalogHash": head.catalog_hash(),
                    "published": outcome.published(),
                }),
            )
        }

        fn resolve_validation_role_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            role: &str,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let resolved = py
                .allow_threads(|| self.coordinator.resolve_validation_role(&keys.inner, role))
                .map_err(corefs_validation_batch_error)?;
            json_value_to_py(
                py,
                match resolved {
                    Some(value) => json!({
                        "generation": value.generation,
                        "catalogHash": value.catalog_hash,
                        "stableId": value.stable_id,
                    }),
                    None => Value::Null,
                },
            )
        }

        fn stat_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            selected_generation: u64,
            selected_catalog_hash: &str,
            path: &str,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let snapshot =
                self.open_read_snapshot(keys, selected_generation, selected_catalog_hash)?;
            corefs_wire_to_py(py, snapshot.stat(path).map_err(corefs_logical_error)?)
        }

        #[pyo3(signature = (keys, selected_generation, selected_catalog_hash, path, cursor_after = None, limit = 100, read_chunk_bytes = None, walk_depth = None, walk_directories = None, walk_entries = None, response_bytes = None))]
        fn list_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            selected_generation: u64,
            selected_catalog_hash: &str,
            path: &str,
            cursor_after: Option<String>,
            limit: usize,
            read_chunk_bytes: Option<usize>,
            walk_depth: Option<usize>,
            walk_directories: Option<usize>,
            walk_entries: Option<usize>,
            response_bytes: Option<usize>,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let limits = corefs_validated_limits(
                read_chunk_bytes,
                walk_depth,
                walk_directories,
                walk_entries,
                response_bytes,
            )?;
            let snapshot =
                self.open_read_snapshot(keys, selected_generation, selected_catalog_hash)?;
            let cursor = cursor_after
                .map(|after| anima_corefs::logical::ListCursor::new(selected_generation, after));
            corefs_wire_to_py(
                py,
                snapshot
                    .list(
                        path,
                        cursor,
                        limit,
                        limits,
                        anima_file_tools::OperationControl::default(),
                    )
                    .map_err(corefs_logical_error)?,
            )
        }

        #[pyo3(signature = (keys, selected_generation, selected_catalog_hash, root, cursor_after = None, page_size = 100, include_directories = true, read_chunk_bytes = None, walk_depth = None, walk_directories = None, walk_entries = None, response_bytes = None))]
        fn walk_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            selected_generation: u64,
            selected_catalog_hash: &str,
            root: &str,
            cursor_after: Option<String>,
            page_size: usize,
            include_directories: bool,
            read_chunk_bytes: Option<usize>,
            walk_depth: Option<usize>,
            walk_directories: Option<usize>,
            walk_entries: Option<usize>,
            response_bytes: Option<usize>,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let limits = corefs_validated_limits(
                read_chunk_bytes,
                walk_depth,
                walk_directories,
                walk_entries,
                response_bytes,
            )?;
            let snapshot =
                self.open_read_snapshot(keys, selected_generation, selected_catalog_hash)?;
            let options = anima_corefs::logical::LogicalWalkOptions {
                page_size,
                cursor: cursor_after.map(|after| {
                    anima_corefs::logical::LogicalWalkCursor::new(selected_generation, after)
                }),
                include_directories,
            };
            corefs_wire_to_py(
                py,
                snapshot
                    .walk(
                        root,
                        options,
                        limits,
                        anima_file_tools::OperationControl::default(),
                    )
                    .map_err(corefs_logical_error)?,
            )
        }

        #[pyo3(signature = (keys, selected_generation, selected_catalog_hash, root, pattern, max_results = 100, cursor_after = None, read_chunk_bytes = None, walk_depth = None, walk_directories = None, walk_entries = None, response_bytes = None))]
        fn glob_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            selected_generation: u64,
            selected_catalog_hash: &str,
            root: &str,
            pattern: &str,
            max_results: usize,
            cursor_after: Option<String>,
            read_chunk_bytes: Option<usize>,
            walk_depth: Option<usize>,
            walk_directories: Option<usize>,
            walk_entries: Option<usize>,
            response_bytes: Option<usize>,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let limits = corefs_validated_limits(
                read_chunk_bytes,
                walk_depth,
                walk_directories,
                walk_entries,
                response_bytes,
            )?;
            let snapshot =
                self.open_read_snapshot(keys, selected_generation, selected_catalog_hash)?;
            let cursor = cursor_after.map(|after| {
                anima_corefs::logical::LogicalGlobCursor::new(selected_generation, after)
            });
            corefs_wire_to_py(
                py,
                snapshot
                    .glob(
                        root,
                        pattern,
                        cursor,
                        max_results,
                        limits,
                        anima_file_tools::OperationControl::default(),
                    )
                    .map_err(corefs_logical_error)?,
            )
        }

        #[pyo3(signature = (keys, selected_generation, selected_catalog_hash, root, query, regex = false, max_files = 1000, max_matches = 100, max_line_bytes = 4096, cursor_path = None, cursor_byte_offset = None, cursor_walk_after = None, read_chunk_bytes = None, walk_depth = None, walk_directories = None, walk_entries = None, response_bytes = None))]
        fn grep_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            selected_generation: u64,
            selected_catalog_hash: &str,
            root: &str,
            query: &str,
            regex: bool,
            max_files: usize,
            max_matches: usize,
            max_line_bytes: usize,
            cursor_path: Option<String>,
            cursor_byte_offset: Option<u64>,
            cursor_walk_after: Option<String>,
            read_chunk_bytes: Option<usize>,
            walk_depth: Option<usize>,
            walk_directories: Option<usize>,
            walk_entries: Option<usize>,
            response_bytes: Option<usize>,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let limits = corefs_validated_limits(
                read_chunk_bytes,
                walk_depth,
                walk_directories,
                walk_entries,
                response_bytes,
            )?;
            let snapshot =
                self.open_read_snapshot(keys, selected_generation, selected_catalog_hash)?;
            let request = anima_corefs::logical::LogicalGrepRequest {
                root: root.to_owned(),
                query: query.to_owned(),
                mode: if regex {
                    anima_file_tools::GrepMode::Regex
                } else {
                    anima_file_tools::GrepMode::Literal
                },
                cursor: cursor_path.map(|path| {
                    anima_corefs::logical::LogicalGrepCursor::new(
                        selected_generation,
                        path,
                        cursor_byte_offset,
                        cursor_walk_after,
                    )
                }),
                max_files,
                max_matches,
                max_line_bytes,
            };
            corefs_wire_to_py(
                py,
                snapshot
                    .grep(
                        request,
                        limits,
                        anima_file_tools::OperationControl::default(),
                    )
                    .map_err(corefs_logical_error)?,
            )
        }

        #[pyo3(signature = (keys, selected_generation, selected_catalog_hash, path, offset = 0, max_bytes = 65536, read_chunk_bytes = None, walk_depth = None, walk_directories = None, walk_entries = None, response_bytes = None))]
        fn read_chunk_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            selected_generation: u64,
            selected_catalog_hash: &str,
            path: &str,
            offset: u64,
            max_bytes: usize,
            read_chunk_bytes: Option<usize>,
            walk_depth: Option<usize>,
            walk_directories: Option<usize>,
            walk_entries: Option<usize>,
            response_bytes: Option<usize>,
        ) -> PyResult<Option<PyObject>> {
            let _operation = self.acquire_operation()?;
            let limits = corefs_validated_limits(
                read_chunk_bytes,
                walk_depth,
                walk_directories,
                walk_entries,
                response_bytes,
            )?;
            let snapshot =
                self.open_read_snapshot(keys, selected_generation, selected_catalog_hash)?;
            let mut stream = snapshot
                .read(
                    path,
                    anima_file_tools::ReadOptions { offset, max_bytes },
                    limits,
                    anima_file_tools::OperationControl::default(),
                )
                .map_err(corefs_logical_error)?;
            stream
                .next()
                .transpose()
                .map_err(corefs_logical_error)?
                .map(|chunk| corefs_wire_to_py(py, chunk))
                .transpose()
        }

        #[pyo3(signature = (keys, selected_generation, selected_catalog_hash, state, index_generation = None))]
        fn search_readiness_v1(
            &self,
            py: Python<'_>,
            keys: &PyCorefsSubkeys,
            selected_generation: u64,
            selected_catalog_hash: &str,
            state: &str,
            index_generation: Option<u64>,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let snapshot =
                self.open_read_snapshot(keys, selected_generation, selected_catalog_hash)?;
            let state = match state {
                "missing" => anima_corefs::logical::RuntimeSearchState::Missing,
                "building" => anima_corefs::logical::RuntimeSearchState::Building {
                    generation: index_generation.ok_or_else(|| {
                        pyo3::exceptions::PyValueError::new_err(
                            "CoreFS search state building requires index_generation",
                        )
                    })?,
                },
                "ready" => anima_corefs::logical::RuntimeSearchState::Ready {
                    generation: index_generation.ok_or_else(|| {
                        pyo3::exceptions::PyValueError::new_err(
                            "CoreFS search state ready requires index_generation",
                        )
                    })?,
                },
                "degraded" => anima_corefs::logical::RuntimeSearchState::Degraded {
                    generation: index_generation.ok_or_else(|| {
                        pyo3::exceptions::PyValueError::new_err(
                            "CoreFS search state degraded requires index_generation",
                        )
                    })?,
                },
                _ => {
                    return Err(pyo3::exceptions::PyValueError::new_err(
                        "CoreFS search state must be missing, building, ready, or degraded",
                    ))
                }
            };
            corefs_wire_to_py(py, snapshot.search_readiness(state))
        }

        fn rotate_frk_v1(
            &self,
            py: Python<'_>,
            retained_keys: Vec<PyRef<'_, PyCorefsSubkeys>>,
            pending_keys: &PyCorefsSubkeys,
            expected_generation: u64,
        ) -> PyResult<PyObject> {
            let _operation = self.acquire_operation()?;
            let keyring = anima_corefs::rotation::FrkKeyring::new(
                retained_keys.iter().map(|keys| &keys.inner),
            )
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
            let outcome = self
                .coordinator
                .rotate_frk(&keyring, &pending_keys.inner, expected_generation, |_| {
                    Ok(())
                })
                .map_err(corefs_commit_error)?;
            json_value_to_py(
                py,
                json!({
                    "generation": outcome.generation(),
                    "catalogHash": outcome.catalog_hash(),
                    "recoveryPending": outcome.recovery_pending(),
                }),
            )
        }
    }

    #[pyclass(name = "CorefsSubkeys")]
    struct PyCorefsSubkeys {
        inner: anima_corefs::crypto::FrkSubkeys,
    }

    #[pymethods]
    impl PyCorefsSubkeys {
        #[getter]
        fn frk_version(&self) -> u32 {
            self.inner.frk_version()
        }
    }

    #[pyclass(name = "CorefsRootKey")]
    struct PyCorefsRootKey {
        inner: anima_corefs::crypto::SecretBytes,
    }

    #[pymethods]
    impl PyCorefsRootKey {
        fn matches(&self, other: &PyCorefsRootKey) -> bool {
            self.inner.as_slice() == other.inner.as_slice()
        }

        fn __eq__(&self, other: &PyCorefsRootKey) -> bool {
            self.matches(other)
        }
    }

    #[pyclass(name = "CorefsObjectDek")]
    struct PyCorefsObjectDek {
        inner: anima_corefs::crypto::SecretBytes,
    }

    #[pymethods]
    impl PyCorefsObjectDek {
        fn matches(&self, other: &PyCorefsObjectDek) -> bool {
            self.inner.as_slice() == other.inner.as_slice()
        }
    }

    #[pyclass(name = "CorefsWrappedRootKey")]
    struct PyCorefsWrappedRootKey {
        inner: anima_corefs::crypto::WrappedFilesystemRootKey,
    }

    #[pymethods]
    impl PyCorefsWrappedRootKey {
        #[new]
        fn new(salt: &str, nonce: &str, tag: &str, ciphertext: &str) -> PyResult<Self> {
            Ok(Self {
                inner: anima_corefs::crypto::WrappedFilesystemRootKey::from_base64_parts(
                    salt, nonce, tag, ciphertext,
                )
                .map_err(corefs_value_error)?,
            })
        }

        #[getter]
        fn kdf_salt(&self) -> String {
            self.inner.salt_base64()
        }

        #[getter]
        fn wrap_iv(&self) -> String {
            self.inner.nonce_base64()
        }

        #[getter]
        fn wrap_tag(&self) -> String {
            self.inner.tag_base64()
        }

        #[getter]
        fn wrapped_key(&self) -> String {
            self.inner.ciphertext_base64()
        }
    }

    #[pyclass(name = "CorefsWrappedObjectDek")]
    struct PyCorefsWrappedObjectDek {
        inner: anima_corefs::crypto::WrappedObjectDek,
    }

    #[pymethods]
    impl PyCorefsWrappedObjectDek {
        #[new]
        fn new(
            algorithm: &str,
            envelope_version: u16,
            nonce: &[u8],
            ciphertext: &[u8],
        ) -> PyResult<Self> {
            Ok(Self {
                inner: anima_corefs::crypto::WrappedObjectDek::from_parts(
                    algorithm,
                    envelope_version,
                    nonce,
                    ciphertext.to_vec(),
                )
                .map_err(corefs_value_error)?,
            })
        }

        #[getter]
        fn algorithm(&self) -> &'static str {
            self.inner.algorithm()
        }

        #[getter]
        fn envelope_version(&self) -> u16 {
            self.inner.envelope_version()
        }

        #[getter]
        fn nonce(&self, py: Python<'_>) -> PyObject {
            PyBytes::new_bound(py, self.inner.nonce()).into_py(py)
        }

        #[getter]
        fn ciphertext(&self, py: Python<'_>) -> PyObject {
            PyBytes::new_bound(py, self.inner.ciphertext()).into_py(py)
        }
    }

    #[allow(clippy::too_many_arguments)]
    fn corefs_aad(
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
        frk_version: u32,
    ) -> PyResult<anima_corefs::crypto::ObjectKeyAad> {
        let kind = anima_corefs::crypto::ObjectKind::parse(kind).map_err(corefs_value_error)?;
        anima_corefs::crypto::ObjectKeyAad::new(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
            frk_version,
        )
        .map_err(corefs_value_error)
    }

    #[pyfunction]
    fn corefs_generate_root_key() -> PyResult<PyCorefsRootKey> {
        Ok(PyCorefsRootKey {
            inner: anima_corefs::crypto::generate_filesystem_root_key()
                .map_err(corefs_value_error)?,
        })
    }

    #[pyfunction]
    fn corefs_atomic_publish(path: &str, payload: &[u8]) -> PyResult<()> {
        anima_corefs::publication::atomic_publish(Path::new(path), payload)
            .map_err(pyo3::exceptions::PyOSError::new_err)
    }

    #[pyfunction]
    #[pyo3(signature = (
        core_id,
        owner_id,
        purpose,
        key_version,
        credential_generation,
        scope,
        frk_version,
        object_key_epoch,
        wrapping_path
    ))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_manifest_keyslot_aad(
        py: Python<'_>,
        core_id: &str,
        owner_id: &str,
        purpose: &str,
        key_version: u32,
        credential_generation: u32,
        scope: &str,
        frk_version: Option<u32>,
        object_key_epoch: Option<u32>,
        wrapping_path: &str,
    ) -> PyResult<PyObject> {
        let aad = anima_corefs::crypto::manifest_keyslot_aad(
            core_id,
            owner_id,
            purpose,
            key_version,
            credential_generation,
            scope,
            frk_version,
            object_key_epoch,
            wrapping_path,
        )
        .map_err(corefs_value_error)?;
        Ok(PyBytes::new_bound(py, &aad).into_py(py))
    }

    #[pyfunction]
    fn corefs_soul_keyslot_aad(
        py: Python<'_>,
        core_id: &str,
        owner_id: &str,
        domain: &str,
        key_version: u32,
        credential_generation: u32,
        wrapping_path: &str,
    ) -> PyResult<PyObject> {
        let aad = anima_corefs::crypto::soul_keyslot_aad(
            core_id,
            owner_id,
            domain,
            key_version,
            credential_generation,
            wrapping_path,
        )
        .map_err(corefs_value_error)?;
        Ok(PyBytes::new_bound(py, &aad).into_py(py))
    }

    #[pyfunction]
    fn corefs_wrap_root_key(
        credential: &str,
        root: &PyCorefsRootKey,
        aad: &[u8],
    ) -> PyResult<PyCorefsWrappedRootKey> {
        Ok(PyCorefsWrappedRootKey {
            inner: anima_corefs::crypto::wrap_filesystem_root_key(credential, &root.inner, aad)
                .map_err(corefs_value_error)?,
        })
    }

    #[pyfunction]
    fn corefs_unwrap_root_key(
        credential: &str,
        wrapped: &PyCorefsWrappedRootKey,
        aad: &[u8],
    ) -> PyResult<PyCorefsRootKey> {
        Ok(PyCorefsRootKey {
            inner: anima_corefs::crypto::unwrap_filesystem_root_key(
                credential,
                &wrapped.inner,
                aad,
            )
            .map_err(corefs_value_error)?,
        })
    }

    #[pyfunction]
    fn corefs_derive_subkeys(frk: &PyCorefsRootKey, frk_version: u32) -> PyResult<PyCorefsSubkeys> {
        Ok(PyCorefsSubkeys {
            inner: anima_corefs::crypto::derive_corefs_subkeys(&frk.inner, frk_version)
                .map_err(corefs_value_error)?,
        })
    }

    #[pyfunction]
    fn corefs_migration_id_v1(domain: &str, source_key: &[u8]) -> PyResult<String> {
        anima_corefs::id::OpaqueId::derive_migration(domain, source_key)
            .map(|value| value.as_str().to_owned())
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))
    }

    #[pyfunction]
    fn corefs_migration_component_v1(value: &str, stable_id: &str) -> PyResult<String> {
        anima_corefs::logical::map_migration_component(value, stable_id)
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))
    }

    #[pyfunction]
    fn corefs_generate_object_dek() -> PyResult<PyCorefsObjectDek> {
        Ok(PyCorefsObjectDek {
            inner: anima_corefs::crypto::generate_object_dek().map_err(corefs_value_error)?,
        })
    }

    #[pyfunction]
    #[pyo3(signature = (keys, object_dek, *, core_id, object_id, revision, kind, envelope_version, object_key_epoch, frk_version))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_wrap_object_dek(
        keys: &PyCorefsSubkeys,
        object_dek: &PyCorefsObjectDek,
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
        frk_version: u32,
    ) -> PyResult<PyCorefsWrappedObjectDek> {
        let aad = corefs_aad(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
            frk_version,
        )?;
        Ok(PyCorefsWrappedObjectDek {
            inner: anima_corefs::crypto::wrap_object_dek(&object_dek.inner, &keys.inner, &aad)
                .map_err(corefs_value_error)?,
        })
    }

    #[pyfunction]
    #[pyo3(signature = (keys, wrapped, *, core_id, object_id, revision, kind, envelope_version, object_key_epoch, frk_version))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_unwrap_object_dek(
        keys: &PyCorefsSubkeys,
        wrapped: &PyCorefsWrappedObjectDek,
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
        frk_version: u32,
    ) -> PyResult<PyCorefsObjectDek> {
        let aad = corefs_aad(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
            frk_version,
        )?;
        Ok(PyCorefsObjectDek {
            inner: anima_corefs::crypto::unwrap_object_dek(&keys.inner, &wrapped.inner, &aad)
                .map_err(corefs_value_error)?,
        })
    }

    #[pyfunction]
    #[pyo3(signature = (*, core_id, object_id, revision, kind, envelope_version, object_key_epoch, frk_version))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_object_key_aad(
        py: Python<'_>,
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
        frk_version: u32,
    ) -> PyResult<PyObject> {
        let aad = corefs_aad(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
            frk_version,
        )?;
        Ok(PyBytes::new_bound(py, &aad.to_bytes()).into_py(py))
    }

    #[allow(clippy::too_many_arguments)]
    fn corefs_base_aad(
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
    ) -> PyResult<anima_corefs::crypto::ObjectBaseAad> {
        let kind = anima_corefs::crypto::ObjectKind::parse(kind).map_err(corefs_value_error)?;
        anima_corefs::crypto::ObjectBaseAad::new(
            core_id,
            object_id,
            kind,
            envelope_version,
            object_key_epoch,
            revision,
        )
        .map_err(corefs_value_error)
    }

    #[pyfunction]
    #[pyo3(signature = (*, core_id, object_id, revision, kind, envelope_version, object_key_epoch))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_object_base_aad(
        py: Python<'_>,
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
    ) -> PyResult<PyObject> {
        let aad = corefs_base_aad(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
        )?;
        Ok(PyBytes::new_bound(py, &aad.to_bytes()).into_py(py))
    }

    #[pyfunction]
    #[pyo3(signature = (*, core_id, object_id, revision, kind, envelope_version, object_key_epoch, chunking_version))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_metadata_frame_aad(
        py: Python<'_>,
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
        chunking_version: u16,
    ) -> PyResult<PyObject> {
        let base = corefs_base_aad(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
        )?;
        let aad = anima_corefs::crypto::MetadataFrameAad::new(base, chunking_version)
            .map_err(corefs_value_error)?;
        Ok(PyBytes::new_bound(py, &aad.to_bytes()).into_py(py))
    }

    #[pyfunction]
    #[pyo3(signature = (*, core_id, object_id, revision, kind, envelope_version, object_key_epoch, metadata_frame_sha256, chunk_index, chunk_count, plaintext_offset, plaintext_length, total_body_length, final_chunk))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_body_frame_aad(
        py: Python<'_>,
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
        metadata_frame_sha256: &[u8],
        chunk_index: u32,
        chunk_count: u32,
        plaintext_offset: u64,
        plaintext_length: u64,
        total_body_length: u64,
        final_chunk: bool,
    ) -> PyResult<PyObject> {
        let base = corefs_base_aad(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
        )?;
        let hash = metadata_frame_sha256.try_into().map_err(|_| {
            pyo3::exceptions::PyValueError::new_err("metadata hash must be 32 bytes")
        })?;
        let aad = anima_corefs::crypto::BodyFrameAad::new(
            base,
            hash,
            chunk_index,
            chunk_count,
            plaintext_offset,
            plaintext_length,
            total_body_length,
            final_chunk,
        )
        .map_err(corefs_value_error)?;
        Ok(PyBytes::new_bound(py, &aad.to_bytes()).into_py(py))
    }

    #[pyfunction]
    fn corefs_fixed_subkey_test_vector(py: Python<'_>) -> PyResult<PyObject> {
        let root =
            anima_corefs::crypto::SecretBytes::new(vec![0x42; 32]).map_err(corefs_value_error)?;
        let keys =
            anima_corefs::crypto::derive_corefs_subkeys(&root, 3).map_err(corefs_value_error)?;
        let values = PyDict::new_bound(py);
        values.set_item(
            "object_wrap",
            PyBytes::new_bound(py, keys.object_wrap().as_slice()),
        )?;
        values.set_item("catalog", PyBytes::new_bound(py, keys.catalog().as_slice()))?;
        values.set_item("search", PyBytes::new_bound(py, keys.search().as_slice()))?;
        Ok(values.into_py(py))
    }

    #[pyfunction]
    #[pyo3(signature = (object_dek, metadata_json, body, *, core_id, object_id, revision, kind, envelope_version, object_key_epoch))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_encrypt_object_envelope(
        py: Python<'_>,
        object_dek: &PyCorefsObjectDek,
        metadata_json: &[u8],
        body: &[u8],
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
    ) -> PyResult<PyObject> {
        enforce_corefs_metadata_limit(metadata_json.len())?;
        let input_length = metadata_json
            .len()
            .checked_add(body.len())
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("CoreFS input is too large"))?;
        enforce_corefs_in_memory_limit(input_length)?;
        let aad = corefs_base_aad(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
        )?;
        let metadata: anima_corefs::envelope::EnvelopeMetadata =
            serde_json::from_slice(metadata_json)
                .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        let encoded =
            anima_corefs::envelope::encode_envelope(&object_dek.inner, &aad, &metadata, body)
                .map_err(corefs_envelope_error)?;
        enforce_corefs_in_memory_limit(encoded.len())?;
        Ok(PyBytes::new_bound(py, &encoded).into_py(py))
    }

    fn corefs_envelope_read_to_py(
        py: Python<'_>,
        read: anima_corefs::envelope::EnvelopeRead,
        body: Option<Vec<u8>>,
    ) -> PyResult<PyObject> {
        let metadata = serde_json::to_vec(&read.metadata)
            .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        let result = PyDict::new_bound(py);
        result.set_item("metadata", PyBytes::new_bound(py, &metadata))?;
        if let Some(body) = body {
            result.set_item("body", PyBytes::new_bound(py, &body))?;
        }
        result.set_item("whole_body_verified", read.whole_body_verified)?;
        Ok(result.into_py(py))
    }

    #[pyfunction]
    #[pyo3(signature = (object_dek, envelope, *, core_id, object_id, revision, kind, envelope_version, object_key_epoch))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_decrypt_object_envelope(
        py: Python<'_>,
        object_dek: &PyCorefsObjectDek,
        envelope: &[u8],
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
    ) -> PyResult<PyObject> {
        enforce_corefs_in_memory_limit(envelope.len())?;
        let aad = corefs_base_aad(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
        )?;
        let (read, body) =
            anima_corefs::envelope::decode_envelope(&object_dek.inner, &aad, envelope)
                .map_err(corefs_envelope_error)?;
        enforce_corefs_in_memory_limit(body.len())?;
        corefs_envelope_read_to_py(py, read, Some(body))
    }

    #[pyfunction]
    #[pyo3(signature = (object_dek, envelope, range_start, range_end, *, core_id, object_id, revision, kind, envelope_version, object_key_epoch))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_read_object_envelope_range(
        py: Python<'_>,
        object_dek: &PyCorefsObjectDek,
        envelope: &[u8],
        range_start: u64,
        range_end: u64,
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
    ) -> PyResult<PyObject> {
        enforce_corefs_in_memory_limit(envelope.len())?;
        let aad = corefs_base_aad(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
        )?;
        let (read, body) = anima_corefs::envelope::decode_envelope_range(
            &object_dek.inner,
            &aad,
            envelope,
            range_start..range_end,
        )
        .map_err(corefs_envelope_error)?;
        enforce_corefs_in_memory_limit(body.len())?;
        corefs_envelope_read_to_py(py, read, Some(body))
    }

    #[pyfunction]
    #[pyo3(signature = (object_dek, metadata_json, body_reader, envelope_writer, *, core_id, object_id, revision, kind, envelope_version, object_key_epoch))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_encrypt_object_envelope_stream(
        _py: Python<'_>,
        object_dek: &PyCorefsObjectDek,
        metadata_json: &[u8],
        body_reader: &Bound<'_, PyAny>,
        envelope_writer: &Bound<'_, PyAny>,
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
    ) -> PyResult<()> {
        enforce_corefs_metadata_limit(metadata_json.len())?;
        let aad = corefs_base_aad(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
        )?;
        let metadata: anima_corefs::envelope::EnvelopeMetadata =
            serde_json::from_slice(metadata_json)
                .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        let mut reader = PyBinaryReader::new(body_reader);
        let mut writer = PyBinaryWriter::new(envelope_writer)?;
        let result = anima_corefs::envelope::write_envelope(
            &mut writer,
            &object_dek.inner,
            &aad,
            &metadata,
            &mut reader,
        );
        finish_corefs_stream(&mut writer, result)
    }

    #[pyfunction]
    #[pyo3(signature = (object_dek, envelope_reader, body_writer, *, core_id, object_id, revision, kind, envelope_version, object_key_epoch))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_decrypt_object_envelope_stream(
        py: Python<'_>,
        object_dek: &PyCorefsObjectDek,
        envelope_reader: &Bound<'_, PyAny>,
        body_writer: &Bound<'_, PyAny>,
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
    ) -> PyResult<PyObject> {
        let aad = corefs_base_aad(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
        )?;
        let mut reader = PyBinaryReader::new(envelope_reader);
        let mut writer = PyBinaryWriter::new(body_writer)?;
        let result = anima_corefs::envelope::read_envelope(
            &mut reader,
            &object_dek.inner,
            &aad,
            &mut writer,
        );
        let read = finish_corefs_stream(&mut writer, result)?;
        corefs_envelope_read_to_py(py, read, None)
            .map_err(|error| rollback_corefs_stream(&mut writer, error))
    }

    #[pyfunction]
    #[pyo3(signature = (object_dek, envelope_reader, body_writer, range_start, range_end, *, core_id, object_id, revision, kind, envelope_version, object_key_epoch))]
    #[allow(clippy::too_many_arguments)]
    fn corefs_read_object_envelope_range_stream(
        py: Python<'_>,
        object_dek: &PyCorefsObjectDek,
        envelope_reader: &Bound<'_, PyAny>,
        body_writer: &Bound<'_, PyAny>,
        range_start: u64,
        range_end: u64,
        core_id: &str,
        object_id: &str,
        revision: u64,
        kind: &str,
        envelope_version: u16,
        object_key_epoch: u32,
    ) -> PyResult<PyObject> {
        let aad = corefs_base_aad(
            core_id,
            object_id,
            revision,
            kind,
            envelope_version,
            object_key_epoch,
        )?;
        let mut reader = PyBinaryReader::new(envelope_reader);
        let mut writer = PyBinaryWriter::new(body_writer)?;
        let result = anima_corefs::envelope::read_envelope_range(
            &mut reader,
            &object_dek.inner,
            &aad,
            range_start..range_end,
            &mut writer,
        );
        let read = finish_corefs_stream(&mut writer, result)?;
        corefs_envelope_read_to_py(py, read, None)
            .map_err(|error| rollback_corefs_stream(&mut writer, error))
    }

    #[pyfunction]
    fn corefs_encode_catalog(py: Python<'_>, payload_json: &[u8]) -> PyResult<PyObject> {
        enforce_corefs_catalog_plaintext_limit(payload_json.len())?;
        let payload: anima_corefs::catalog::CatalogPayload =
            serde_json::from_slice(payload_json)
                .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
        let encoded =
            anima_corefs::catalog::encode_catalog(&payload).map_err(corefs_catalog_error)?;
        Ok(PyBytes::new_bound(py, &encoded).into_py(py))
    }

    #[pyfunction]
    fn corefs_decode_catalog(py: Python<'_>, payload_json: &[u8]) -> PyResult<PyObject> {
        let payload =
            anima_corefs::catalog::decode_catalog(payload_json).map_err(corefs_catalog_error)?;
        let encoded =
            anima_corefs::catalog::encode_catalog(&payload).map_err(corefs_catalog_error)?;
        Ok(PyBytes::new_bound(py, &encoded).into_py(py))
    }

    #[pyfunction]
    fn corefs_encrypt_catalog(
        py: Python<'_>,
        keys: &PyCorefsSubkeys,
        core_id: &str,
        payload_json: &[u8],
    ) -> PyResult<PyObject> {
        let payload =
            anima_corefs::catalog::decode_catalog(payload_json).map_err(corefs_catalog_error)?;
        let encrypted = anima_corefs::catalog::encrypt_catalog(&keys.inner, core_id, &payload)
            .map_err(corefs_catalog_error)?;
        Ok(PyBytes::new_bound(py, &encrypted).into_py(py))
    }

    #[pyfunction]
    fn corefs_decrypt_catalog(
        py: Python<'_>,
        keys: &PyCorefsSubkeys,
        core_id: &str,
        envelope: &[u8],
    ) -> PyResult<PyObject> {
        let payload = anima_corefs::catalog::decrypt_catalog(&keys.inner, core_id, envelope)
            .map_err(corefs_catalog_error)?;
        let encoded =
            anima_corefs::catalog::encode_catalog(&payload).map_err(corefs_catalog_error)?;
        Ok(PyBytes::new_bound(py, &encoded).into_py(py))
    }

    #[pyfunction]
    fn corefs_catalog_physical_name(generation: u64, envelope: &[u8]) -> PyResult<String> {
        enforce_corefs_catalog_envelope_limit(envelope.len())?;
        anima_corefs::catalog::catalog_physical_name(generation, envelope)
            .map_err(corefs_catalog_error)
    }

    #[pyfunction]
    fn corefs_validation_snapshot(
        py: Python<'_>,
        core_root: &str,
        core_id: &str,
        keys: &PyCorefsSubkeys,
    ) -> PyResult<PyObject> {
        let coordinator = anima_corefs::transaction::CoreCommitCoordinator::new(core_root, core_id)
            .map_err(corefs_commit_error)?;
        let selected = coordinator
            .load_validation_snapshot(&keys.inner)
            .map_err(corefs_commit_error)?
            .ok_or_else(|| {
                pyo3::exceptions::PyValueError::new_err("CoreFS validation snapshot is missing")
            })?;
        let head = selected.head();
        json_value_to_py(
            py,
            json!({
                "generation": head.generation(),
                "catalogHash": head.catalog_hash(),
            }),
        )
    }

    #[pyfunction]
    fn corefs_stat_v1(
        py: Python<'_>,
        core_root: &str,
        core_id: &str,
        keys: &PyCorefsSubkeys,
        selected_generation: u64,
        selected_catalog_hash: &str,
        path: &str,
    ) -> PyResult<PyObject> {
        let snapshot = corefs_open_read_snapshot(
            core_root,
            core_id,
            keys,
            selected_generation,
            selected_catalog_hash,
        )?;
        corefs_wire_to_py(py, snapshot.stat(path).map_err(corefs_logical_error)?)
    }

    #[pyfunction]
    #[pyo3(signature = (core_root, core_id, keys, selected_generation, selected_catalog_hash, path, cursor_after = None, limit = 100, read_chunk_bytes = None, walk_depth = None, walk_directories = None, walk_entries = None, response_bytes = None))]
    #[allow(clippy::too_many_arguments)] // Stable Python ABI exposes each validated limit.
    fn corefs_list_v1(
        py: Python<'_>,
        core_root: &str,
        core_id: &str,
        keys: &PyCorefsSubkeys,
        selected_generation: u64,
        selected_catalog_hash: &str,
        path: &str,
        cursor_after: Option<String>,
        limit: usize,
        read_chunk_bytes: Option<usize>,
        walk_depth: Option<usize>,
        walk_directories: Option<usize>,
        walk_entries: Option<usize>,
        response_bytes: Option<usize>,
    ) -> PyResult<PyObject> {
        let limits = corefs_validated_limits(
            read_chunk_bytes,
            walk_depth,
            walk_directories,
            walk_entries,
            response_bytes,
        )?;
        let snapshot = corefs_open_read_snapshot(
            core_root,
            core_id,
            keys,
            selected_generation,
            selected_catalog_hash,
        )?;
        let cursor = cursor_after
            .map(|after| anima_corefs::logical::ListCursor::new(selected_generation, after));
        corefs_wire_to_py(
            py,
            snapshot
                .list(
                    path,
                    cursor,
                    limit,
                    limits,
                    anima_file_tools::OperationControl::default(),
                )
                .map_err(corefs_logical_error)?,
        )
    }

    #[pyfunction]
    #[pyo3(signature = (core_root, core_id, keys, selected_generation, selected_catalog_hash, root, cursor_after = None, page_size = 100, include_directories = true, read_chunk_bytes = None, walk_depth = None, walk_directories = None, walk_entries = None, response_bytes = None))]
    #[allow(clippy::too_many_arguments)] // Stable Python ABI exposes each validated limit.
    fn corefs_walk_v1(
        py: Python<'_>,
        core_root: &str,
        core_id: &str,
        keys: &PyCorefsSubkeys,
        selected_generation: u64,
        selected_catalog_hash: &str,
        root: &str,
        cursor_after: Option<String>,
        page_size: usize,
        include_directories: bool,
        read_chunk_bytes: Option<usize>,
        walk_depth: Option<usize>,
        walk_directories: Option<usize>,
        walk_entries: Option<usize>,
        response_bytes: Option<usize>,
    ) -> PyResult<PyObject> {
        let limits = corefs_validated_limits(
            read_chunk_bytes,
            walk_depth,
            walk_directories,
            walk_entries,
            response_bytes,
        )?;
        let snapshot = corefs_open_read_snapshot(
            core_root,
            core_id,
            keys,
            selected_generation,
            selected_catalog_hash,
        )?;
        let options = anima_corefs::logical::LogicalWalkOptions {
            page_size,
            cursor: cursor_after.map(|after| {
                anima_corefs::logical::LogicalWalkCursor::new(selected_generation, after)
            }),
            include_directories,
        };
        corefs_wire_to_py(
            py,
            snapshot
                .walk(
                    root,
                    options,
                    limits,
                    anima_file_tools::OperationControl::default(),
                )
                .map_err(corefs_logical_error)?,
        )
    }

    #[pyfunction]
    #[pyo3(signature = (core_root, core_id, keys, selected_generation, selected_catalog_hash, root, pattern, max_results = 100, cursor_after = None, read_chunk_bytes = None, walk_depth = None, walk_directories = None, walk_entries = None, response_bytes = None))]
    #[allow(clippy::too_many_arguments)] // Stable Python ABI exposes each validated limit.
    fn corefs_glob_v1(
        py: Python<'_>,
        core_root: &str,
        core_id: &str,
        keys: &PyCorefsSubkeys,
        selected_generation: u64,
        selected_catalog_hash: &str,
        root: &str,
        pattern: &str,
        max_results: usize,
        cursor_after: Option<String>,
        read_chunk_bytes: Option<usize>,
        walk_depth: Option<usize>,
        walk_directories: Option<usize>,
        walk_entries: Option<usize>,
        response_bytes: Option<usize>,
    ) -> PyResult<PyObject> {
        let limits = corefs_validated_limits(
            read_chunk_bytes,
            walk_depth,
            walk_directories,
            walk_entries,
            response_bytes,
        )?;
        let snapshot = corefs_open_read_snapshot(
            core_root,
            core_id,
            keys,
            selected_generation,
            selected_catalog_hash,
        )?;
        let cursor = cursor_after
            .map(|after| anima_corefs::logical::LogicalGlobCursor::new(selected_generation, after));
        corefs_wire_to_py(
            py,
            snapshot
                .glob(
                    root,
                    pattern,
                    cursor,
                    max_results,
                    limits,
                    anima_file_tools::OperationControl::default(),
                )
                .map_err(corefs_logical_error)?,
        )
    }

    #[pyfunction]
    #[pyo3(signature = (core_root, core_id, keys, selected_generation, selected_catalog_hash, root, query, regex = false, max_files = 1000, max_matches = 100, max_line_bytes = 4096, cursor_path = None, cursor_byte_offset = None, cursor_walk_after = None, read_chunk_bytes = None, walk_depth = None, walk_directories = None, walk_entries = None, response_bytes = None))]
    #[allow(clippy::too_many_arguments)] // Stable Python ABI exposes each bounded grep option.
    fn corefs_grep_v1(
        py: Python<'_>,
        core_root: &str,
        core_id: &str,
        keys: &PyCorefsSubkeys,
        selected_generation: u64,
        selected_catalog_hash: &str,
        root: &str,
        query: &str,
        regex: bool,
        max_files: usize,
        max_matches: usize,
        max_line_bytes: usize,
        cursor_path: Option<String>,
        cursor_byte_offset: Option<u64>,
        cursor_walk_after: Option<String>,
        read_chunk_bytes: Option<usize>,
        walk_depth: Option<usize>,
        walk_directories: Option<usize>,
        walk_entries: Option<usize>,
        response_bytes: Option<usize>,
    ) -> PyResult<PyObject> {
        let limits = corefs_validated_limits(
            read_chunk_bytes,
            walk_depth,
            walk_directories,
            walk_entries,
            response_bytes,
        )?;
        let snapshot = corefs_open_read_snapshot(
            core_root,
            core_id,
            keys,
            selected_generation,
            selected_catalog_hash,
        )?;
        let request = anima_corefs::logical::LogicalGrepRequest {
            root: root.to_owned(),
            query: query.to_owned(),
            mode: if regex {
                anima_file_tools::GrepMode::Regex
            } else {
                anima_file_tools::GrepMode::Literal
            },
            cursor: cursor_path.map(|path| {
                anima_corefs::logical::LogicalGrepCursor::new(
                    selected_generation,
                    path,
                    cursor_byte_offset,
                    cursor_walk_after,
                )
            }),
            max_files,
            max_matches,
            max_line_bytes,
        };
        corefs_wire_to_py(
            py,
            snapshot
                .grep(
                    request,
                    limits,
                    anima_file_tools::OperationControl::default(),
                )
                .map_err(corefs_logical_error)?,
        )
    }

    #[pyfunction]
    #[pyo3(signature = (core_root, core_id, keys, selected_generation, selected_catalog_hash, path, offset = 0, max_bytes = 65536, read_chunk_bytes = None, walk_depth = None, walk_directories = None, walk_entries = None, response_bytes = None))]
    #[allow(clippy::too_many_arguments)] // Stable Python ABI exposes each validated limit.
    fn corefs_read_chunk_v1(
        py: Python<'_>,
        core_root: &str,
        core_id: &str,
        keys: &PyCorefsSubkeys,
        selected_generation: u64,
        selected_catalog_hash: &str,
        path: &str,
        offset: u64,
        max_bytes: usize,
        read_chunk_bytes: Option<usize>,
        walk_depth: Option<usize>,
        walk_directories: Option<usize>,
        walk_entries: Option<usize>,
        response_bytes: Option<usize>,
    ) -> PyResult<Option<PyObject>> {
        let limits = corefs_validated_limits(
            read_chunk_bytes,
            walk_depth,
            walk_directories,
            walk_entries,
            response_bytes,
        )?;
        let snapshot = corefs_open_read_snapshot(
            core_root,
            core_id,
            keys,
            selected_generation,
            selected_catalog_hash,
        )?;
        let mut stream = snapshot
            .read(
                path,
                anima_file_tools::ReadOptions { offset, max_bytes },
                limits,
                anima_file_tools::OperationControl::default(),
            )
            .map_err(corefs_logical_error)?;
        stream
            .next()
            .transpose()
            .map_err(corefs_logical_error)?
            .map(|chunk| corefs_wire_to_py(py, chunk))
            .transpose()
    }

    #[pyfunction]
    #[pyo3(signature = (core_root, core_id, keys, selected_generation, selected_catalog_hash, state, index_generation = None))]
    #[allow(clippy::too_many_arguments)] // Stable Python ABI carries authenticated snapshot identity.
    fn corefs_search_readiness_v1(
        py: Python<'_>,
        core_root: &str,
        core_id: &str,
        keys: &PyCorefsSubkeys,
        selected_generation: u64,
        selected_catalog_hash: &str,
        state: &str,
        index_generation: Option<u64>,
    ) -> PyResult<PyObject> {
        let snapshot = corefs_open_read_snapshot(
            core_root,
            core_id,
            keys,
            selected_generation,
            selected_catalog_hash,
        )?;
        let state = match state {
            "missing" => anima_corefs::logical::RuntimeSearchState::Missing,
            "building" => anima_corefs::logical::RuntimeSearchState::Building {
                generation: index_generation.ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err(
                        "CoreFS search state building requires index_generation",
                    )
                })?,
            },
            "ready" => anima_corefs::logical::RuntimeSearchState::Ready {
                generation: index_generation.ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err(
                        "CoreFS search state ready requires index_generation",
                    )
                })?,
            },
            "degraded" => anima_corefs::logical::RuntimeSearchState::Degraded {
                generation: index_generation.ok_or_else(|| {
                    pyo3::exceptions::PyValueError::new_err(
                        "CoreFS search state degraded requires index_generation",
                    )
                })?,
            },
            _ => {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "CoreFS search state must be missing, building, ready, or degraded",
                ))
            }
        };
        corefs_wire_to_py(py, snapshot.search_readiness(state))
    }

    fn corefs_frozen_result(py: Python<'_>, operation: &str) -> PyResult<PyObject> {
        json_value_to_py(
            py,
            json!({
                "ok": false,
                "operation": operation,
                "code": anima_corefs::logical::CORE_FS_MIGRATION_WRITE_FROZEN,
            }),
        )
    }

    #[pyfunction]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn corefs_mkdir(
        py: Python<'_>,
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<PyObject> {
        corefs_frozen_result(py, "mkdir")
    }

    #[pyfunction]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn corefs_create_file(
        py: Python<'_>,
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<PyObject> {
        corefs_frozen_result(py, "create_file")
    }

    #[pyfunction]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn corefs_write_file(
        py: Python<'_>,
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<PyObject> {
        corefs_frozen_result(py, "write_file")
    }

    #[pyfunction]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn corefs_apply_patch(
        py: Python<'_>,
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<PyObject> {
        corefs_frozen_result(py, "apply_patch")
    }

    #[pyfunction]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn corefs_move(
        py: Python<'_>,
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<PyObject> {
        corefs_frozen_result(py, "move")
    }

    #[pyfunction]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn corefs_trash(
        py: Python<'_>,
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<PyObject> {
        corefs_frozen_result(py, "trash")
    }

    #[pyfunction]
    #[pyo3(signature = (*_args, **_kwargs))]
    fn corefs_restore(
        py: Python<'_>,
        _args: &Bound<'_, PyTuple>,
        _kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<PyObject> {
        corefs_frozen_result(py, "restore")
    }

    fn json_value_to_py(py: Python<'_>, value: Value) -> PyResult<PyObject> {
        match value {
            Value::Null => Ok(py.None()),
            Value::Bool(value) => Ok(value.into_py(py)),
            Value::Number(value) => {
                if let Some(value) = value.as_i64() {
                    Ok(value.into_py(py))
                } else if let Some(value) = value.as_u64() {
                    Ok(value.into_py(py))
                } else if let Some(value) = value.as_f64() {
                    Ok(value.into_py(py))
                } else {
                    Err(pyo3::exceptions::PyValueError::new_err(
                        "unsupported numeric value",
                    ))
                }
            }
            Value::String(value) => Ok(value.into_py(py)),
            Value::Array(values) => {
                let list = PyList::empty_bound(py);
                for value in values {
                    list.append(json_value_to_py(py, value)?)?;
                }
                Ok(list.into_py(py))
            }
            Value::Object(values) => {
                let dict = PyDict::new_bound(py);
                for (key, value) in values {
                    dict.set_item(key, json_value_to_py(py, value)?)?;
                }
                Ok(dict.into_py(py))
            }
        }
    }

    fn integrity_report_to_py_dict(py: Python<'_>, report: &IntegrityReport) -> PyResult<PyObject> {
        let value = serde_json::to_value(report)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    fn core_stats_to_py_dict(py: Python<'_>, stats: &CoreStats) -> PyResult<PyObject> {
        let value = serde_json::to_value(stats)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    fn capsule_report_to_py_dict(
        py: Python<'_>,
        report: &CapsuleIntegrityReport,
    ) -> PyResult<PyObject> {
        let value = serde_json::to_value(report)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    fn parse_frame_kind(kind: &str) -> PyResult<FrameKind> {
        match kind {
            "fact" => Ok(FrameKind::Fact),
            "preference" => Ok(FrameKind::Preference),
            "goal" => Ok(FrameKind::Goal),
            "relationship" => Ok(FrameKind::Relationship),
            "episode" => Ok(FrameKind::Episode),
            "claim" => Ok(FrameKind::Claim),
            "emotional_signal" => Ok(FrameKind::EmotionalSignal),
            "self_model" => Ok(FrameKind::SelfModel),
            "kg_node" => Ok(FrameKind::KgNode),
            "kg_edge" => Ok(FrameKind::KgEdge),
            "focus" => Ok(FrameKind::Focus),
            "daily_log" => Ok(FrameKind::DailyLog),
            "growth_log" => Ok(FrameKind::GrowthLog),
            "identity" => Ok(FrameKind::Identity),
            other => Err(pyo3::exceptions::PyValueError::new_err(format!(
                "unknown frame kind: {other}"
            ))),
        }
    }

    fn parse_memory_kind(kind: &str) -> PyResult<MemoryKind> {
        match kind.to_lowercase().as_str() {
            "fact" => Ok(MemoryKind::Fact),
            "preference" => Ok(MemoryKind::Preference),
            "event" => Ok(MemoryKind::Event),
            "profile" => Ok(MemoryKind::Profile),
            "relationship" => Ok(MemoryKind::Relationship),
            "goal" => Ok(MemoryKind::Goal),
            "other" => Ok(MemoryKind::Other),
            other => Err(pyo3::exceptions::PyValueError::new_err(format!(
                "unknown memory kind: {other}"
            ))),
        }
    }

    fn parse_version_relation(version: &str) -> PyResult<VersionRelation> {
        match version.to_lowercase().as_str() {
            "sets" => Ok(VersionRelation::Sets),
            "updates" => Ok(VersionRelation::Updates),
            "extends" => Ok(VersionRelation::Extends),
            "retracts" => Ok(VersionRelation::Retracts),
            other => Err(pyo3::exceptions::PyValueError::new_err(format!(
                "unknown version relation: {other}"
            ))),
        }
    }

    fn parse_entity_kind(kind: &str) -> PyResult<EntityKind> {
        match kind.to_lowercase().as_str() {
            "person" => Ok(EntityKind::Person),
            "organization" => Ok(EntityKind::Organization),
            "project" => Ok(EntityKind::Project),
            "location" => Ok(EntityKind::Location),
            "event" => Ok(EntityKind::Event),
            "product" => Ok(EntityKind::Product),
            "email" => Ok(EntityKind::Email),
            "date" => Ok(EntityKind::Date),
            "url" => Ok(EntityKind::Url),
            "other" => Ok(EntityKind::Other),
            other => Err(pyo3::exceptions::PyValueError::new_err(format!(
                "unknown entity kind: {other}"
            ))),
        }
    }

    // ── Frame Bindings ───────────────────────────────────────────────

    #[pyclass(name = "Frame")]
    #[derive(Clone)]
    struct PyFrame {
        inner: Frame,
    }

    #[pymethods]
    impl PyFrame {
        #[new]
        #[pyo3(signature = (kind, content, user_id))]
        fn new(kind: &str, content: String, user_id: String) -> PyResult<Self> {
            let fk = parse_frame_kind(kind)?;
            Ok(Self {
                inner: Frame::new(0, fk, content, user_id, FrameSource::Api),
            })
        }

        #[getter]
        fn id(&self) -> u64 {
            self.inner.id
        }

        #[getter]
        fn kind(&self) -> String {
            self.inner.kind.as_str().to_string()
        }

        #[getter]
        fn content(&self) -> &str {
            &self.inner.content
        }

        #[getter]
        fn timestamp(&self) -> i64 {
            self.inner.timestamp
        }

        #[getter]
        fn user_id(&self) -> &str {
            &self.inner.user_id
        }

        #[getter]
        fn checksum(&self) -> String {
            hex::encode(self.inner.checksum)
        }

        fn verify_checksum(&self) -> bool {
            self.inner.verify_checksum()
        }

        fn to_json(&self) -> PyResult<String> {
            serde_json::to_string(&self.inner)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
        }
    }

    // ── FrameStore Bindings ──────────────────────────────────────────

    #[pyclass(name = "FrameStore")]
    struct PyFrameStore {
        inner: FrameStore,
    }

    #[pyclass(name = "TemporalIndex")]
    struct PyTemporalIndex {
        inner: TemporalIndex,
        store: FrameStore,
    }

    #[pyclass(name = "Engine")]
    struct PyAnimaEngine {
        inner: crate::engine::AnimaEngine,
    }

    #[pymethods]
    impl PyFrameStore {
        #[new]
        fn new() -> Self {
            Self {
                inner: FrameStore::new(),
            }
        }

        fn insert(&mut self, frame: &PyFrame) -> u64 {
            self.inner.insert(frame.inner.clone())
        }

        fn get(&self, id: u64) -> Option<PyFrame> {
            self.inner.get(id).map(|f| PyFrame { inner: f.clone() })
        }

        fn len(&self) -> usize {
            self.inner.len()
        }

        fn temporal_index(&self) -> PyTemporalIndex {
            PyTemporalIndex {
                inner: TemporalIndex::from_store(&self.inner),
                store: self.inner.clone(),
            }
        }

        #[pyo3(signature = (start = None, end = None, limit = None))]
        fn temporal_range(
            &self,
            start: Option<i64>,
            end: Option<i64>,
            limit: Option<usize>,
        ) -> Vec<PyFrame> {
            let index = TemporalIndex::from_store(&self.inner);
            index
                .range(&self.inner, start, end, limit)
                .into_iter()
                .map(|frame| PyFrame {
                    inner: frame.clone(),
                })
                .collect()
        }

        #[pyo3(signature = (timestamp, limit = None))]
        fn temporal_as_of(&self, timestamp: i64, limit: Option<usize>) -> Vec<PyFrame> {
            let index = TemporalIndex::from_store(&self.inner);
            index
                .as_of(&self.inner, timestamp, limit)
                .into_iter()
                .map(|frame| PyFrame {
                    inner: frame.clone(),
                })
                .collect()
        }

        #[pyo3(signature = (session_data, padding_before_secs = 0, padding_after_secs = 0, limit = None))]
        fn temporal_session_window(
            &self,
            session_data: Vec<u8>,
            padding_before_secs: i64,
            padding_after_secs: i64,
            limit: Option<usize>,
        ) -> PyResult<Vec<PyFrame>> {
            let session = crate::replay::ReplaySession::deserialize(&session_data)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            let index = TemporalIndex::from_store(&self.inner);
            Ok(index
                .session_window(
                    &self.inner,
                    &session,
                    padding_before_secs,
                    padding_after_secs,
                    limit,
                )
                .into_iter()
                .map(|frame| PyFrame {
                    inner: frame.clone(),
                })
                .collect())
        }
    }

    #[pymethods]
    impl PyTemporalIndex {
        fn len(&self) -> usize {
            self.inner.len()
        }

        fn is_empty(&self) -> bool {
            self.inner.is_empty()
        }

        #[pyo3(signature = (start = None, end = None, limit = None))]
        fn range(
            &self,
            start: Option<i64>,
            end: Option<i64>,
            limit: Option<usize>,
        ) -> Vec<PyFrame> {
            self.inner
                .range(&self.store, start, end, limit)
                .into_iter()
                .map(|frame| PyFrame {
                    inner: frame.clone(),
                })
                .collect()
        }

        #[pyo3(signature = (timestamp, limit = None))]
        fn as_of(&self, timestamp: i64, limit: Option<usize>) -> Vec<PyFrame> {
            self.inner
                .as_of(&self.store, timestamp, limit)
                .into_iter()
                .map(|frame| PyFrame {
                    inner: frame.clone(),
                })
                .collect()
        }

        #[pyo3(signature = (session_data, padding_before_secs = 0, padding_after_secs = 0, limit = None))]
        fn session_window(
            &self,
            session_data: Vec<u8>,
            padding_before_secs: i64,
            padding_after_secs: i64,
            limit: Option<usize>,
        ) -> PyResult<Vec<PyFrame>> {
            let session = crate::replay::ReplaySession::deserialize(&session_data)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            Ok(self
                .inner
                .session_window(
                    &self.store,
                    &session,
                    padding_before_secs,
                    padding_after_secs,
                    limit,
                )
                .into_iter()
                .map(|frame| PyFrame {
                    inner: frame.clone(),
                })
                .collect())
        }
    }

    #[pymethods]
    impl PyAnimaEngine {
        #[new]
        fn new() -> Self {
            Self {
                inner: crate::engine::AnimaEngine::new(),
            }
        }

        #[staticmethod]
        #[pyo3(signature = (data, password = None))]
        fn from_capsule_bytes(data: Vec<u8>, password: Option<Vec<u8>>) -> PyResult<Self> {
            let inner = crate::engine::AnimaEngine::read_capsule(data, password.as_deref())
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            Ok(Self { inner })
        }

        #[pyo3(signature = (password = None))]
        fn to_capsule_bytes(&self, password: Option<Vec<u8>>) -> PyResult<Vec<u8>> {
            self.inner
                .write_capsule(password.as_deref())
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
        }

        fn verify(&self, py: Python<'_>) -> PyResult<PyObject> {
            integrity_report_to_py_dict(py, &self.inner.verify())
        }

        fn stats(&self, py: Python<'_>) -> PyResult<PyObject> {
            core_stats_to_py_dict(py, &self.inner.stats())
        }

        fn project_entity_state(&self, py: Python<'_>, entity: &str) -> PyResult<PyObject> {
            let value = serde_json::to_value(self.inner.entity_state(entity))
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            json_value_to_py(py, value)
        }

        fn project_slot_history(
            &self,
            py: Python<'_>,
            entity: &str,
            slot: &str,
        ) -> PyResult<PyObject> {
            let value = serde_json::to_value(self.inner.slot_history(entity, slot))
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            json_value_to_py(py, value)
        }

        #[pyo3(signature = (start = None, end = None, limit = None))]
        fn temporal_range(
            &self,
            py: Python<'_>,
            start: Option<i64>,
            end: Option<i64>,
            limit: Option<usize>,
        ) -> PyResult<PyObject> {
            let value = serde_json::to_value(self.inner.temporal_range(start, end, limit))
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
            json_value_to_py(py, value)
        }
    }

    // ── SIMD distance functions ──────────────────────────────────────

    #[pyfunction]
    fn l2_distance(a: Vec<f32>, b: Vec<f32>) -> PyResult<f32> {
        if a.len() != b.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "vectors must have same length",
            ));
        }
        Ok(crate::simd::l2_distance(&a, &b))
    }

    #[pyfunction]
    fn cosine_similarity(a: Vec<f32>, b: Vec<f32>) -> PyResult<f32> {
        if a.len() != b.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "vectors must have same length",
            ));
        }
        Ok(crate::simd::cosine_similarity(&a, &b))
    }

    #[pyfunction]
    fn cosine_distance(a: Vec<f32>, b: Vec<f32>) -> PyResult<f32> {
        if a.len() != b.len() {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "vectors must have same length",
            ));
        }
        Ok(crate::simd::cosine_distance(&a, &b))
    }

    #[pyfunction]
    fn normalize_scores(scores: Vec<f32>) -> Vec<f32> {
        crate::adaptive::normalize_scores(&scores)
    }

    #[pyfunction]
    #[pyo3(signature = (scores, strategy = "combined", min_results = 1, max_results = 100, normalize = true, absolute_min = 0.3, relative_threshold = 0.5, max_drop_ratio = 0.4, sensitivity = 1.0))]
    #[allow(clippy::too_many_arguments)] // Python API intentionally exposes the cutoff policy.
    fn find_adaptive_cutoff(
        scores: Vec<f32>,
        strategy: &str,
        min_results: usize,
        max_results: usize,
        normalize: bool,
        absolute_min: f32,
        relative_threshold: f32,
        max_drop_ratio: f32,
        sensitivity: f32,
    ) -> PyResult<(usize, String, Vec<f32>)> {
        let mut config = crate::adaptive::AdaptiveConfig {
            max_results,
            min_results,
            normalize_scores: normalize,
            ..Default::default()
        };

        config.enabled = strategy != "disabled";
        config.strategy = match strategy {
            "absolute_threshold" => crate::adaptive::CutoffStrategy::AbsoluteThreshold {
                min_score: absolute_min,
            },
            "relative_threshold" => crate::adaptive::CutoffStrategy::RelativeThreshold {
                min_ratio: relative_threshold,
            },
            "score_cliff" => crate::adaptive::CutoffStrategy::ScoreCliff { max_drop_ratio },
            "elbow" => crate::adaptive::CutoffStrategy::Elbow { sensitivity },
            "combined" | "disabled" => crate::adaptive::CutoffStrategy::Combined {
                relative_threshold,
                max_drop_ratio,
                absolute_min,
            },
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unknown adaptive strategy: {other}"
                )))
            }
        };

        Ok(crate::adaptive::find_adaptive_cutoff(&scores, &config))
    }

    // ── HNSW Bindings ────────────────────────────────────────────────

    #[cfg(feature = "hnsw")]
    mod hnsw_bindings {
        use super::*;
        use crate::hnsw::HnswIndex;

        #[pyclass(name = "HnswIndex")]
        pub struct PyHnswIndex {
            inner: HnswIndex,
        }

        #[pymethods]
        impl PyHnswIndex {
            #[new]
            fn new(dimensions: usize) -> Self {
                Self {
                    inner: HnswIndex::new(dimensions),
                }
            }

            fn insert(&mut self, id: u64, embedding: Vec<f32>) -> PyResult<()> {
                self.inner
                    .insert(id, embedding)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
            }

            fn search(&mut self, query: Vec<f32>, k: usize) -> PyResult<Vec<(u64, f32)>> {
                self.inner
                    .search(&query, k)
                    .map(|results| {
                        results
                            .into_iter()
                            .map(|result| (result.frame_id, result.distance))
                            .collect()
                    })
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
            }

            fn remove(&mut self, id: u64) {
                self.inner.remove(id);
            }

            fn len(&self) -> usize {
                self.inner.len()
            }
        }
    }

    // ── CardStore Bindings ───────────────────────────────────────────

    #[pyclass(name = "CardStore")]
    struct PyCardStore {
        inner: CardStore,
    }

    #[pymethods]
    impl PyCardStore {
        #[new]
        fn new() -> Self {
            Self {
                inner: CardStore::new(SchemaRegistry::new()),
            }
        }

        #[pyo3(signature = (entity, slot, value, kind = "fact", version = "sets", confidence = 1.0, frame_id = 0))]
        #[allow(clippy::too_many_arguments)] // Python card API mirrors the portable card schema.
        fn put(
            &mut self,
            entity: &str,
            slot: &str,
            value: &str,
            kind: &str,
            version: &str,
            confidence: f32,
            frame_id: u64,
        ) -> PyResult<u64> {
            let card = MemoryCard {
                id: 0,
                kind: parse_memory_kind(kind)?,
                entity: entity.into(),
                slot: slot.into(),
                value: value.into(),
                polarity: Polarity::Neutral,
                version: parse_version_relation(version)?,
                confidence,
                frame_id,
                created_at: chrono::Utc::now().timestamp(),
                active: true,
                superseded_by: None,
            };
            Ok(self.inner.put(card))
        }

        fn get_current(&self, entity: &str, slot: &str) -> Vec<String> {
            self.inner
                .get_current(entity, slot)
                .into_iter()
                .map(|c| c.value.clone())
                .collect()
        }

        fn get_history(&self, entity: &str, slot: &str) -> Vec<String> {
            self.inner
                .get_history(entity, slot)
                .into_iter()
                .map(|c| c.value.clone())
                .collect()
        }

        fn len(&self) -> usize {
            self.inner.len()
        }

        fn active_count(&self) -> usize {
            self.inner.active_count()
        }

        fn set_cardinality(&mut self, entity_pattern: &str, slot: &str, multiple: bool) {
            let c = if multiple {
                Cardinality::Multiple
            } else {
                Cardinality::Single
            };
            self.inner.schema.set(entity_pattern, slot, c);
        }
    }

    // ── KnowledgeGraph Bindings ──────────────────────────────────────

    #[pyclass(name = "KnowledgeGraph")]
    struct PyKnowledgeGraph {
        inner: KnowledgeGraph,
    }

    #[pymethods]
    impl PyKnowledgeGraph {
        #[new]
        fn new() -> Self {
            Self {
                inner: KnowledgeGraph::new(),
            }
        }

        fn upsert_node(
            &mut self,
            name: &str,
            kind: &str,
            confidence: f32,
            frame_id: u64,
        ) -> PyResult<u64> {
            self.inner
                .upsert_node(name, parse_entity_kind(kind)?, confidence, frame_id)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        }

        fn upsert_edge(
            &mut self,
            from_node: u64,
            to_node: u64,
            relation_type: &str,
            confidence: f32,
            frame_id: u64,
        ) -> PyResult<()> {
            self.inner
                .upsert_edge(from_node, to_node, relation_type, confidence, frame_id)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        }

        #[pyo3(signature = (start_name, relation_filter = None, max_hops = 2))]
        fn follow(
            &self,
            start_name: &str,
            relation_filter: Option<&str>,
            max_hops: usize,
        ) -> Vec<(String, String, f32, usize)> {
            self.inner
                .follow(start_name, relation_filter, max_hops)
                .into_iter()
                .map(|r| {
                    (
                        r.node_name,
                        r.node_kind.as_str().to_string(),
                        r.confidence,
                        r.path_length,
                    )
                })
                .collect()
        }

        fn node_count(&self) -> usize {
            self.inner.node_count()
        }

        fn edge_count(&self) -> usize {
            self.inner.edge_count()
        }
    }

    // ── Temporal Bindings ────────────────────────────────────────────

    #[pyfunction]
    fn project_entity_state(
        py: Python<'_>,
        cards: &PyCardStore,
        graph: &PyKnowledgeGraph,
        entity: &str,
    ) -> PyResult<PyObject> {
        let state = crate::projection::entity_state_from_cards_and_graph(
            &cards.inner,
            &graph.inner,
            entity,
        );
        let value = serde_json::to_value(state)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pyfunction]
    fn project_slot_history(
        py: Python<'_>,
        cards: &PyCardStore,
        entity: &str,
        slot: &str,
    ) -> PyResult<PyObject> {
        let history = crate::projection::slot_history(&cards.inner, entity, slot);
        let value = serde_json::to_value(history)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pyfunction]
    fn parse_temporal(input: &str) -> Option<(String, i64, f32)> {
        let now = chrono::Utc::now();
        crate::temporal::parse_temporal(input, now).map(|m| (m.raw, m.timestamp, m.confidence))
    }

    #[pyfunction]
    fn replay_session_time_bounds(data: Vec<u8>) -> PyResult<Option<(i64, i64)>> {
        let session = crate::replay::ReplaySession::deserialize(&data)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        Ok(session.time_bounds())
    }

    #[pyfunction]
    fn replay_session_checkpoints(py: Python<'_>, data: Vec<u8>) -> PyResult<PyObject> {
        let session = crate::replay::ReplaySession::deserialize(&data)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let checkpoints = session.checkpoints();
        let value = serde_json::to_value(checkpoints)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pyfunction]
    fn replay_session_checkpoint_by_seq(
        py: Python<'_>,
        data: Vec<u8>,
        seq: u32,
    ) -> PyResult<Option<PyObject>> {
        let session = crate::replay::ReplaySession::deserialize(&data)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        session
            .checkpoint_by_seq(seq)
            .map(|checkpoint| {
                let value = serde_json::to_value(checkpoint)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
                json_value_to_py(py, value)
            })
            .transpose()
    }

    #[pyfunction]
    fn replay_session_checkpoint_by_label(
        py: Python<'_>,
        data: Vec<u8>,
        label: &str,
    ) -> PyResult<Option<PyObject>> {
        let session = crate::replay::ReplaySession::deserialize(&data)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        session
            .checkpoint_by_label(label)
            .map(|checkpoint| {
                let value = serde_json::to_value(checkpoint)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
                json_value_to_py(py, value)
            })
            .transpose()
    }

    #[pyfunction]
    fn compare_replay_sessions(
        py: Python<'_>,
        left: Vec<u8>,
        right: Vec<u8>,
    ) -> PyResult<PyObject> {
        let left_session = crate::replay::ReplaySession::deserialize(&left)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let right_session = crate::replay::ReplaySession::deserialize(&right)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let comparison = left_session.compare(&right_session);
        let value = serde_json::to_value(comparison)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pyfunction]
    fn replay_session_summary(py: Python<'_>, data: Vec<u8>) -> PyResult<PyObject> {
        let session = crate::replay::ReplaySession::deserialize(&data)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        let summary = session.structured_summary();
        let value = serde_json::to_value(summary)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    // ── Capsule Bindings ─────────────────────────────────────────────

    fn replay_registry_from_bytes(
        sessions: Vec<Vec<u8>>,
    ) -> PyResult<crate::replay::ReplayRegistry> {
        let sessions = sessions
            .into_iter()
            .map(|data| {
                crate::replay::ReplaySession::deserialize(&data)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
            })
            .collect::<PyResult<Vec<_>>>()?;
        crate::replay::ReplayRegistry::from_sessions(sessions)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn replay_registry_session_summary(
        py: Python<'_>,
        sessions: Vec<Vec<u8>>,
        session_id: &str,
    ) -> PyResult<Option<PyObject>> {
        let registry = replay_registry_from_bytes(sessions)?;
        registry
            .summary(session_id)
            .map(|summary| {
                let value = serde_json::to_value(summary)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
                json_value_to_py(py, value)
            })
            .transpose()
    }

    #[pyfunction]
    fn replay_registry_session_ids(sessions: Vec<Vec<u8>>) -> PyResult<Vec<String>> {
        let registry = replay_registry_from_bytes(sessions)?;
        Ok(registry.session_ids())
    }

    #[pyfunction]
    fn replay_registry_checkpoint_by_seq(
        py: Python<'_>,
        sessions: Vec<Vec<u8>>,
        session_id: &str,
        seq: u32,
    ) -> PyResult<Option<PyObject>> {
        let registry = replay_registry_from_bytes(sessions)?;
        registry
            .checkpoint_by_seq(session_id, seq)
            .map(|checkpoint| {
                let value = serde_json::to_value(checkpoint)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
                json_value_to_py(py, value)
            })
            .transpose()
    }

    #[pyfunction]
    fn replay_registry_checkpoint_by_label(
        py: Python<'_>,
        sessions: Vec<Vec<u8>>,
        session_id: &str,
        label: &str,
    ) -> PyResult<Option<PyObject>> {
        let registry = replay_registry_from_bytes(sessions)?;
        registry
            .checkpoint_by_label(session_id, label)
            .map(|checkpoint| {
                let value = serde_json::to_value(checkpoint)
                    .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
                json_value_to_py(py, value)
            })
            .transpose()
    }

    #[pyfunction]
    #[pyo3(signature = (sections, password = None))]
    fn write_capsule(
        sections: HashMap<String, Vec<u8>>,
        password: Option<Vec<u8>>,
    ) -> PyResult<Vec<u8>> {
        use crate::capsule::{CapsuleWriter, SectionKind};

        #[cfg(feature = "encryption")]
        let mut writer = if let Some(password) = password {
            CapsuleWriter::new().with_password(password)
        } else {
            CapsuleWriter::new()
        };

        #[cfg(not(feature = "encryption"))]
        let mut writer = {
            if password.is_some() {
                return Err(pyo3::exceptions::PyRuntimeError::new_err(
                    "anima_core was built without capsule encryption support",
                ));
            }
            CapsuleWriter::new()
        };

        for (key, data) in sections {
            let kind = match key.as_str() {
                "frames" => SectionKind::Frames,
                "cards" => SectionKind::Cards,
                "graph" => SectionKind::Graph,
                "metadata" => SectionKind::Metadata,
                _ => {
                    return Err(pyo3::exceptions::PyValueError::new_err(format!(
                        "unknown section: {key}"
                    )));
                }
            };
            writer.add_section(kind, data);
        }

        writer
            .write()
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    #[pyo3(signature = (data, password = None))]
    fn read_capsule(
        data: Vec<u8>,
        password: Option<Vec<u8>>,
    ) -> PyResult<HashMap<String, Vec<u8>>> {
        use crate::capsule::{CapsuleReader, SectionKind};

        let reader = CapsuleReader::open(data, password.as_deref())
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        let mut result = HashMap::new();
        for kind in reader.sections() {
            let key = match kind {
                SectionKind::Frames => "frames",
                SectionKind::Cards => "cards",
                SectionKind::Graph => "graph",
                SectionKind::Metadata => "metadata",
            };
            match reader.read_section(kind) {
                Ok(data) => {
                    result.insert(key.to_string(), data);
                }
                Err(e) => {
                    return Err(pyo3::exceptions::PyRuntimeError::new_err(e.to_string()));
                }
            }
        }

        Ok(result)
    }

    #[pyfunction]
    fn verify_frame_store(py: Python<'_>, store: &PyFrameStore) -> PyResult<PyObject> {
        integrity_report_to_py_dict(py, &scan_frame_store(&store.inner))
    }

    #[pyfunction]
    fn frame_store_stats(py: Python<'_>, store: &PyFrameStore) -> PyResult<PyObject> {
        let report = scan_frame_store(&store.inner);
        core_stats_to_py_dict(py, &report.stats)
    }

    #[pyfunction]
    #[pyo3(signature = (data, password = None))]
    fn verify_capsule_bytes(
        py: Python<'_>,
        data: Vec<u8>,
        password: Option<Vec<u8>>,
    ) -> PyResult<PyObject> {
        let report = verify_capsule_integrity(&data, password.as_deref());
        capsule_report_to_py_dict(py, &report)
    }

    // ── Text Bindings ────────────────────────────────────────────────

    #[pyfunction]
    #[pyo3(signature = (text, limit = 4096))]
    fn normalize_text(text: &str, limit: usize) -> Option<(String, bool)> {
        crate::text::normalize_text(text, limit)
            .map(|normalized| (normalized.text, normalized.truncated))
    }

    #[pyfunction]
    fn truncate_at_grapheme_boundary(text: &str, limit: usize) -> usize {
        crate::text::truncate_at_grapheme_boundary(text, limit)
    }

    #[pyfunction]
    fn fix_pdf_spacing(text: &str) -> String {
        crate::text::fix_pdf_spacing(text)
    }

    type ExtractedTriplet = (String, String, String, String, String, f32, usize, usize);

    #[pyfunction]
    fn extract_triplets(text: &str) -> Vec<ExtractedTriplet> {
        crate::triplet::extract_triplets(text)
            .into_iter()
            .map(|triplet| {
                (
                    triplet.subject,
                    triplet.subject_type,
                    triplet.predicate,
                    triplet.object,
                    triplet.object_type,
                    triplet.confidence,
                    triplet.char_start,
                    triplet.char_end,
                )
            })
            .collect()
    }

    // ── Chunker Bindings ─────────────────────────────────────────────

    #[pyclass(name = "ChunkOptions")]
    #[derive(Clone)]
    struct PyChunkOptions {
        _inner: crate::chunker::ChunkOptions,
    }

    #[pymethods]
    impl PyChunkOptions {
        #[new]
        #[pyo3(signature = (max_chars = 1200, overlap_chars = 0, preserve_code_blocks = true, preserve_tables = true, include_section_headers = true, preserve_lists = true))]
        fn new(
            max_chars: usize,
            overlap_chars: usize,
            preserve_code_blocks: bool,
            preserve_tables: bool,
            include_section_headers: bool,
            preserve_lists: bool,
        ) -> Self {
            Self {
                _inner: crate::chunker::ChunkOptions {
                    max_chars,
                    overlap_chars,
                    preserve_code_blocks,
                    preserve_tables,
                    include_section_headers,
                    preserve_lists,
                },
            }
        }
    }

    #[pyfunction]
    #[pyo3(signature = (text, max_chars = 1200, overlap_chars = 0))]
    fn chunk_text(
        text: &str,
        max_chars: usize,
        overlap_chars: usize,
    ) -> Vec<(String, String, usize, usize, usize)> {
        let opts = crate::chunker::ChunkOptions {
            max_chars,
            overlap_chars,
            ..Default::default()
        };
        crate::chunker::chunk_text(text, &opts)
            .into_iter()
            .map(|c| {
                let type_str = format!("{:?}", c.chunk_type).to_lowercase();
                (c.text, type_str, c.index, c.char_start, c.char_end)
            })
            .collect()
    }

    // ── Enrich Bindings ──────────────────────────────────────────────

    #[pyclass(name = "RulesEngine")]
    struct PyRulesEngine {
        inner: crate::enrich::RulesEngine,
    }

    #[pymethods]
    impl PyRulesEngine {
        #[new]
        #[pyo3(signature = (use_defaults = true))]
        fn new(use_defaults: bool) -> Self {
            Self {
                inner: if use_defaults {
                    crate::enrich::RulesEngine::new()
                } else {
                    crate::enrich::RulesEngine::empty()
                },
            }
        }

        fn add_rule(
            &mut self,
            name: &str,
            pattern: &str,
            kind: &str,
            entity_template: &str,
            slot_template: &str,
            value_template: &str,
        ) -> PyResult<()> {
            let kind = parse_memory_kind(kind)?;
            let rule = crate::enrich::ExtractionRule::new(
                name,
                pattern,
                kind,
                entity_template,
                slot_template,
                value_template,
            )
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("invalid regex pattern"))?;
            self.inner.add_rule(rule);
            Ok(())
        }

        fn rule_count(&self) -> usize {
            self.inner.rule_count()
        }

        /// Returns list of (rule_name, entity, slot, value, kind, confidence, char_start, char_end)
        fn extract(&self, text: &str) -> Vec<ExtractedTriplet> {
            self.inner
                .extract(text)
                .into_iter()
                .map(|e| {
                    (
                        e.rule_name,
                        e.entity,
                        e.slot,
                        e.value,
                        e.kind.as_str().to_string(),
                        e.confidence,
                        e.char_start,
                        e.char_end,
                    )
                })
                .collect()
        }

        fn extract_above(&self, text: &str, min_confidence: f32) -> Vec<ExtractedTriplet> {
            self.inner
                .extract_above(text, min_confidence)
                .into_iter()
                .map(|e| {
                    (
                        e.rule_name,
                        e.entity,
                        e.slot,
                        e.value,
                        e.kind.as_str().to_string(),
                        e.confidence,
                        e.char_start,
                        e.char_end,
                    )
                })
                .collect()
        }
    }

    // ── Search Bindings ──────────────────────────────────────────────

    #[pyfunction]
    fn rrf_fuse(ranked_lists: Vec<Vec<(u64, f32)>>, k: u32) -> Vec<(u64, f32)> {
        let vec_results = ranked_lists.first().map(Vec::as_slice).unwrap_or(&[]);
        let lex_results = ranked_lists.get(1).map(Vec::as_slice).unwrap_or(&[]);
        crate::search::rrf_fuse(vec_results, lex_results, k as f32)
            .into_iter()
            .map(|(frame_id, score, _vec_rank, _lex_rank)| (frame_id, score))
            .collect()
    }

    #[pyfunction]
    #[pyo3(signature = (access_count, depth, importance, seconds_since_access, superseded = false))]
    fn compute_heat(
        access_count: u32,
        depth: u32,
        importance: f32,
        seconds_since_access: f64,
        superseded: bool,
    ) -> f64 {
        let now = chrono::Utc::now().timestamp();
        let age_seconds = seconds_since_access.max(0.0).round() as i64;
        let meta = HeatMeta {
            access_count,
            interaction_depth: depth as f64,
            importance: importance.round().clamp(0.0, 5.0) as u8,
            last_accessed_at: Some(now - age_seconds),
            is_superseded: superseded,
        };
        crate::search::compute_heat(&meta, &HeatParams::default(), now)
    }

    // ── Module Registration ──────────────────────────────────────────

    #[pyfunction]
    fn retrieval_manifest_status(py: Python<'_>, root: &str) -> PyResult<PyObject> {
        let root = Path::new(root);
        let (exists, corrupt, manifest) = crate::retrieval_index::manifest_status(root)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        json_value_to_py(
            py,
            json!({
                "exists": exists,
                "corrupt": corrupt,
                "version": manifest.version,
                "families": manifest.families,
            }),
        )
    }

    #[pyfunction]
    fn mark_retrieval_index_dirty(root: &str, family: &str) -> PyResult<()> {
        let family = match family {
            "memory" => crate::retrieval_index::IndexFamily::Memory,
            "transcript" => crate::retrieval_index::IndexFamily::Transcript,
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unknown retrieval index family: {other}"
                )))
            }
        };
        crate::retrieval_index::mark_family_dirty(Path::new(root), family)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn clear_retrieval_index_dirty(root: &str, family: &str) -> PyResult<()> {
        let family = match family {
            "memory" => crate::retrieval_index::IndexFamily::Memory,
            "transcript" => crate::retrieval_index::IndexFamily::Transcript,
            other => {
                return Err(pyo3::exceptions::PyValueError::new_err(format!(
                    "unknown retrieval index family: {other}"
                )))
            }
        };
        crate::retrieval_index::clear_family_dirty(Path::new(root), family)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    #[pyo3(signature = (root, record_id, user_id, text, source_type, category, importance, created_at, embedding=None))]
    #[allow(clippy::too_many_arguments)] // Python API mirrors the durable memory record schema.
    fn memory_index_upsert(
        root: &str,
        record_id: u64,
        user_id: u64,
        text: &str,
        source_type: &str,
        category: &str,
        importance: u8,
        created_at: i64,
        embedding: Option<Vec<f32>>,
    ) -> PyResult<()> {
        crate::retrieval_index::upsert_memory_document(
            Path::new(root),
            crate::retrieval_index::MemoryIndexDocument {
                record_id,
                user_id,
                text: text.to_owned(),
                embedding,
                source_type: source_type.to_owned(),
                category: category.to_owned(),
                importance,
                created_at,
            },
        )
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn memory_index_delete(root: &str, record_id: u64, user_id: u64) -> PyResult<bool> {
        crate::retrieval_index::delete_memory_document(Path::new(root), user_id, record_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn memory_index_delete_user_documents(root: &str, user_id: u64) -> PyResult<u64> {
        crate::retrieval_index::delete_memory_documents_for_user(Path::new(root), user_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn reset_memory_index(root: &str) -> PyResult<()> {
        crate::retrieval_index::reset_memory_documents(Path::new(root))
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn memory_index_search(
        py: Python<'_>,
        root: &str,
        user_id: u64,
        query: &str,
        limit: usize,
    ) -> PyResult<PyObject> {
        let hits =
            crate::retrieval_index::search_memory_documents(Path::new(root), user_id, query, limit)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        let value = serde_json::to_value(hits)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pyfunction]
    fn memory_index_vector_search(
        py: Python<'_>,
        root: &str,
        user_id: u64,
        query_embedding: Vec<f32>,
        limit: usize,
    ) -> PyResult<PyObject> {
        let hits = crate::retrieval_index::search_memory_documents_by_vector(
            Path::new(root),
            user_id,
            &query_embedding,
            limit,
        )
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        let value = serde_json::to_value(hits)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pyfunction]
    #[allow(clippy::too_many_arguments)] // Python API mirrors the durable transcript schema.
    fn transcript_index_upsert(
        root: &str,
        thread_id: u64,
        user_id: u64,
        transcript_ref: &str,
        summary: &str,
        keywords: Vec<String>,
        text: &str,
        date_start: i64,
    ) -> PyResult<()> {
        crate::retrieval_index::upsert_transcript_document(
            Path::new(root),
            crate::retrieval_index::TranscriptIndexDocument {
                thread_id,
                user_id,
                transcript_ref: transcript_ref.to_owned(),
                summary: summary.to_owned(),
                keywords,
                text: text.to_owned(),
                date_start,
            },
        )
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn transcript_index_delete(root: &str, thread_id: u64, user_id: u64) -> PyResult<bool> {
        crate::retrieval_index::delete_transcript_document(Path::new(root), user_id, thread_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn transcript_index_delete_user_documents(root: &str, user_id: u64) -> PyResult<u64> {
        crate::retrieval_index::delete_transcript_documents_for_user(Path::new(root), user_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn reset_transcript_index(root: &str) -> PyResult<()> {
        crate::retrieval_index::reset_transcript_documents(Path::new(root))
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
    }

    #[pyfunction]
    fn transcript_index_search(
        py: Python<'_>,
        root: &str,
        user_id: u64,
        query: &str,
        limit: usize,
    ) -> PyResult<PyObject> {
        let hits = crate::retrieval_index::search_transcript_documents(
            Path::new(root),
            user_id,
            query,
            limit,
        )
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        let value = serde_json::to_value(hits)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        json_value_to_py(py, value)
    }

    #[pymodule]
    #[pyo3(name = "anima_core")]
    pub fn anima_core_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
        // Frame types
        m.add_class::<PyFrame>()?;
        m.add_class::<PyFrameStore>()?;
        m.add_class::<PyTemporalIndex>()?;
        m.add_class::<PyAnimaEngine>()?;
        m.add_class::<PyCorefsSubkeys>()?;
        m.add_class::<PyCorefsRootKey>()?;
        m.add_class::<PyCorefsObjectDek>()?;
        m.add_class::<PyCorefsWrappedRootKey>()?;
        m.add_class::<PyCorefsWrappedObjectDek>()?;
        m.add_class::<PyCorefsSession>()?;
        m.add(
            "CorefsPreparationConflictError",
            m.py().get_type_bound::<CorefsPreparationConflictError>(),
        )?;
        m.add(
            "CorefsPreparationCorruptionError",
            m.py().get_type_bound::<CorefsPreparationCorruptionError>(),
        )?;
        m.add(
            "CorefsPreparationSourceFenceError",
            m.py().get_type_bound::<CorefsPreparationSourceFenceError>(),
        )?;
        m.add_function(wrap_pyfunction!(corefs_atomic_publish, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_manifest_keyslot_aad, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_soul_keyslot_aad, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_generate_root_key, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_wrap_root_key, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_unwrap_root_key, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_derive_subkeys, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_migration_id_v1, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_migration_component_v1, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_generate_object_dek, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_wrap_object_dek, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_unwrap_object_dek, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_object_key_aad, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_object_base_aad, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_metadata_frame_aad, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_body_frame_aad, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_fixed_subkey_test_vector, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_encrypt_object_envelope, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_decrypt_object_envelope, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_read_object_envelope_range, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_encrypt_object_envelope_stream, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_decrypt_object_envelope_stream, m)?)?;
        m.add_function(wrap_pyfunction!(
            corefs_read_object_envelope_range_stream,
            m
        )?)?;
        m.add_function(wrap_pyfunction!(corefs_encode_catalog, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_decode_catalog, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_encrypt_catalog, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_decrypt_catalog, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_catalog_physical_name, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_validation_snapshot, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_stat_v1, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_list_v1, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_walk_v1, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_glob_v1, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_grep_v1, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_read_chunk_v1, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_search_readiness_v1, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_mkdir, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_create_file, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_write_file, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_apply_patch, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_move, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_trash, m)?)?;
        m.add_function(wrap_pyfunction!(corefs_restore, m)?)?;

        // SIMD functions
        m.add_function(wrap_pyfunction!(l2_distance, m)?)?;
        m.add_function(wrap_pyfunction!(cosine_similarity, m)?)?;
        m.add_function(wrap_pyfunction!(cosine_distance, m)?)?;
        m.add_function(wrap_pyfunction!(normalize_scores, m)?)?;
        m.add_function(wrap_pyfunction!(find_adaptive_cutoff, m)?)?;

        // HNSW
        #[cfg(feature = "hnsw")]
        m.add_class::<hnsw_bindings::PyHnswIndex>()?;

        // Cards
        m.add_class::<PyCardStore>()?;

        // Knowledge Graph
        m.add_class::<PyKnowledgeGraph>()?;
        m.add_function(wrap_pyfunction!(project_entity_state, m)?)?;
        m.add_function(wrap_pyfunction!(project_slot_history, m)?)?;

        // Temporal
        m.add_function(wrap_pyfunction!(parse_temporal, m)?)?;
        m.add_function(wrap_pyfunction!(replay_session_time_bounds, m)?)?;
        m.add_function(wrap_pyfunction!(replay_session_checkpoints, m)?)?;
        m.add_function(wrap_pyfunction!(replay_session_checkpoint_by_seq, m)?)?;
        m.add_function(wrap_pyfunction!(replay_session_checkpoint_by_label, m)?)?;
        m.add_function(wrap_pyfunction!(compare_replay_sessions, m)?)?;
        m.add_function(wrap_pyfunction!(replay_session_summary, m)?)?;
        m.add_function(wrap_pyfunction!(replay_registry_session_summary, m)?)?;
        m.add_function(wrap_pyfunction!(replay_registry_session_ids, m)?)?;
        m.add_function(wrap_pyfunction!(replay_registry_checkpoint_by_seq, m)?)?;
        m.add_function(wrap_pyfunction!(replay_registry_checkpoint_by_label, m)?)?;

        // Capsule
        m.add_function(wrap_pyfunction!(write_capsule, m)?)?;
        m.add_function(wrap_pyfunction!(read_capsule, m)?)?;
        m.add_function(wrap_pyfunction!(verify_frame_store, m)?)?;
        m.add_function(wrap_pyfunction!(frame_store_stats, m)?)?;
        m.add_function(wrap_pyfunction!(verify_capsule_bytes, m)?)?;

        // Text
        m.add_function(wrap_pyfunction!(normalize_text, m)?)?;
        m.add_function(wrap_pyfunction!(truncate_at_grapheme_boundary, m)?)?;
        m.add_function(wrap_pyfunction!(fix_pdf_spacing, m)?)?;
        m.add_function(wrap_pyfunction!(extract_triplets, m)?)?;

        // Search
        m.add_function(wrap_pyfunction!(rrf_fuse, m)?)?;
        m.add_function(wrap_pyfunction!(compute_heat, m)?)?;
        m.add_function(wrap_pyfunction!(retrieval_manifest_status, m)?)?;
        m.add_function(wrap_pyfunction!(mark_retrieval_index_dirty, m)?)?;
        m.add_function(wrap_pyfunction!(clear_retrieval_index_dirty, m)?)?;
        m.add_function(wrap_pyfunction!(memory_index_upsert, m)?)?;
        m.add_function(wrap_pyfunction!(memory_index_delete, m)?)?;
        m.add_function(wrap_pyfunction!(memory_index_delete_user_documents, m)?)?;
        m.add_function(wrap_pyfunction!(reset_memory_index, m)?)?;
        m.add_function(wrap_pyfunction!(memory_index_search, m)?)?;
        m.add_function(wrap_pyfunction!(memory_index_vector_search, m)?)?;
        m.add_function(wrap_pyfunction!(transcript_index_upsert, m)?)?;
        m.add_function(wrap_pyfunction!(transcript_index_delete, m)?)?;
        m.add_function(wrap_pyfunction!(transcript_index_delete_user_documents, m)?)?;
        m.add_function(wrap_pyfunction!(reset_transcript_index, m)?)?;
        m.add_function(wrap_pyfunction!(transcript_index_search, m)?)?;

        // Chunker
        m.add_class::<PyChunkOptions>()?;
        m.add_function(wrap_pyfunction!(chunk_text, m)?)?;

        // Enrich
        m.add_class::<PyRulesEngine>()?;

        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::fs;
        use std::io::Cursor;
        use std::panic::{catch_unwind, AssertUnwindSafe};
        use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
        #[cfg(windows)]
        use std::sync::Mutex;
        use std::sync::{Arc, Barrier, Once};
        use std::thread;
        use std::time::{Duration, Instant};

        use anima_corefs::catalog::{
            CatalogEntryCommon, CatalogGeneration, CatalogGenerationEntry, CatalogObject,
            ObjectLifecycle,
        };
        use anima_corefs::crypto::{
            derive_corefs_subkeys, FrkSubkeys, ObjectBaseAad, ObjectKind, SecretBytes,
        };
        use anima_corefs::envelope::{
            encode_envelope, BodyEncoding, EnvelopeMetadata, ENVELOPE_VERSION,
        };
        use anima_corefs::folders::{FolderOwner, PortableName};
        use anima_corefs::id::OpaqueId;
        use anima_corefs::policy::AnimaAccess;
        #[cfg(windows)]
        use anima_corefs::transaction::session_test_support::{
            SessionLeaseUsage, SessionPublicationPause, WindowsSessionLeaseControl,
        };
        use anima_corefs::transaction::{
            CoreCommitCoordinator, PreparedObjectRevision, ValidationSnapshot,
        };

        use crate::capsule::{CapsuleWriter, SectionKind};
        use crate::frame::{Frame, FrameKind, FrameSource};
        use crate::integrity::{scan_frame_store, IntegrityIssueKind};

        const OBJECT_ID: &str = "01J00000000000000000000000";
        const STREAM_ID: &str = "01J00000000000000000000001";
        const CATALOG_ID: &str = "01J00000000000000000000002";
        const LOGICAL_CORE_ID: &str = "ffi-logical-core";
        const LOGICAL_ROOT_ID: &str = "01J20000000000000000000000";
        const LOGICAL_NOTES_ID: &str = "01J20000000000000000000001";
        const LOGICAL_OBJECT_ID: &str = "01J20000000000000000000002";

        struct LogicalFixture {
            _root: tempfile::TempDir,
            core_root: String,
            keys: PyCorefsSubkeys,
            selected: ValidationSnapshot,
            physical_name: String,
        }

        impl LogicalFixture {
            fn root_path(&self) -> &str {
                &self.core_root
            }
        }

        fn with_python<T>(f: impl FnOnce(Python<'_>) -> T) -> T {
            static INIT: Once = Once::new();
            INIT.call_once(pyo3::prepare_freethreaded_python);
            Python::with_gil(f)
        }

        mod corefs_preparation {
            use super::*;

            const FFI_SOURCE: &str = include_str!("ffi.rs");

            fn session_methods() -> &'static str {
                FFI_SOURCE
                    .split("impl PyCorefsSession {")
                    .nth(2)
                    .expect("PyCorefsSession pymethods impl must exist")
                    .split("#[pyclass(name = \"CorefsSubkeys\")]")
                    .next()
                    .expect("CorefsSubkeys must follow session methods")
            }

            #[test]
            fn exposes_only_the_versioned_bounded_preparation_surface() {
                for method in [
                    "fn preparation_begin_or_resume_v1(",
                    "fn preparation_status_v1(",
                    "fn preparation_prepare_object_v1(",
                    "fn preparation_seal_v1(",
                    "fn preparation_finalize_v1(",
                    "fn preparation_abandon_v1(",
                    "fn preparation_quarantine_corrupt_pointer_v1(",
                ] {
                    assert!(session_methods().contains(method), "missing {method}");
                }
            }

            #[test]
            fn one_object_method_has_no_aggregate_body_transport() {
                let method = session_methods()
                    .split("fn preparation_prepare_object_v1(")
                    .nth(1)
                    .expect("preparation_prepare_object_v1 must exist")
                    .split("\n        fn preparation_seal_v1(")
                    .next()
                    .expect("preparation_seal_v1 must follow prepare_object");
                assert!(method.contains("body: PyBuffer<u8>"));
                for forbidden in [
                    "Vec<Vec<u8>>",
                    "Vec<PyBytes",
                    "content_parts",
                    "contentBodies",
                ] {
                    assert!(
                        !method.contains(forbidden),
                        "one-object preparation accepted forbidden aggregate transport {forbidden}"
                    );
                }
            }

            #[test]
            fn every_preparation_method_rejects_after_close_begins() {
                let (_root, session) = super::corefs_session::native_session(
                    "preparation-closed",
                    "ffi-preparation-closed",
                );
                session.begin_close_native();

                with_python(|py| {
                    let keys = Py::new(
                        py,
                        PyCorefsSubkeys {
                            inner: derive_corefs_subkeys(
                                &SecretBytes::new(vec![0x71; 32]).unwrap(),
                                1,
                            )
                            .unwrap(),
                        },
                    )
                    .unwrap();
                    let assert_closed = |error: PyErr| {
                        assert!(error.is_instance_of::<pyo3::exceptions::PyRuntimeError>(py));
                        assert!(error.to_string().contains("CoreFS session"));
                    };

                    assert_closed(
                        session
                            .preparation_begin_or_resume_v1(py, &keys.borrow(py), "{}")
                            .unwrap_err(),
                    );
                    assert_closed(
                        session
                            .preparation_status_v1(py, &keys.borrow(py), None)
                            .unwrap_err(),
                    );
                    assert_closed(
                        session
                            .preparation_prepare_object_v1(
                                py,
                                &keys.borrow(py),
                                "{}",
                                PyBuffer::get_bound(&PyBytes::new_bound(py, b"one body")).unwrap(),
                            )
                            .unwrap_err(),
                    );
                    assert_closed(
                        session
                            .preparation_seal_v1(py, &keys.borrow(py), "{}")
                            .unwrap_err(),
                    );
                    assert_closed(
                        session
                            .preparation_finalize_v1(py, &keys.borrow(py), "{}")
                            .unwrap_err(),
                    );
                    assert_closed(
                        session
                            .preparation_abandon_v1(py, &keys.borrow(py), "{}")
                            .unwrap_err(),
                    );
                    assert_closed(
                        session
                            .preparation_quarantine_corrupt_pointer_v1(
                                py,
                                vec![keys.borrow(py)],
                                &keys.borrow(py),
                                "{}",
                            )
                            .unwrap_err(),
                    );
                });
                session.close_native().unwrap();
            }

            #[test]
            fn begin_status_and_newer_source_reconciliation_are_secret_free() {
                let (_root, session) =
                    super::corefs_session::native_session("preparation-roundtrip", OBJECT_ID);
                let keys = PyCorefsSubkeys {
                    inner: derive_corefs_subkeys(&SecretBytes::new(vec![0x72; 32]).unwrap(), 1)
                        .unwrap(),
                };
                let owner_id = OpaqueId::derive_migration("owner", b"ffi-preparation").unwrap();
                let request = |generation: u64, digest: char| {
                    json!({
                        "scope": "pcf004-writing-v1",
                        "expectedValidationGeneration": null,
                        "expectedValidationCatalogSha256": null,
                        "sourceOwnerId": owner_id.as_str(),
                        "sourceSchemaVersion": 1,
                        "sourceMutationGeneration": generation,
                        "sourceInventorySha256": digest.to_string().repeat(64),
                    })
                    .to_string()
                };

                with_python(|py| {
                    let begun = session
                        .preparation_begin_or_resume_v1(py, &keys, &request(1, 'a'))
                        .unwrap();
                    let begun = begun.bind(py).downcast::<PyDict>().unwrap();
                    assert_eq!(
                        begun
                            .get_item("disposition")
                            .unwrap()
                            .unwrap()
                            .extract::<String>()
                            .unwrap(),
                        "begun"
                    );
                    for secret in [
                        "snapshotCiphertextSha256",
                        "physicalName",
                        "wrappedObjectDek",
                        "requiredFrkVersion",
                    ] {
                        assert!(begun.get_item(secret).unwrap().is_none());
                    }

                    let reconciled = session
                        .preparation_begin_or_resume_v1(py, &keys, &request(2, 'b'))
                        .unwrap();
                    let reconciled = reconciled.bind(py).downcast::<PyDict>().unwrap();
                    assert_eq!(
                        reconciled
                            .get_item("disposition")
                            .unwrap()
                            .unwrap()
                            .extract::<String>()
                            .unwrap(),
                        "reconciled"
                    );
                    assert_eq!(
                        reconciled
                            .get_item("sourceMutationGeneration")
                            .unwrap()
                            .unwrap()
                            .extract::<u64>()
                            .unwrap(),
                        2
                    );

                    let stale = session
                        .preparation_begin_or_resume_v1(py, &keys, &request(1, 'a'))
                        .unwrap_err();
                    assert!(stale.is_instance_of::<CorefsPreparationSourceFenceError>(py));
                    let conflict = session
                        .preparation_begin_or_resume_v1(py, &keys, &request(2, 'c'))
                        .unwrap_err();
                    assert!(conflict.is_instance_of::<CorefsPreparationConflictError>(py));
                });
            }

            #[test]
            fn prepares_exactly_one_body_and_pages_reconciliation_metadata() {
                let (_root, session) =
                    super::corefs_session::native_session("preparation-one-body", OBJECT_ID);
                let keys = PyCorefsSubkeys {
                    inner: derive_corefs_subkeys(&SecretBytes::new(vec![0x73; 32]).unwrap(), 1)
                        .unwrap(),
                };
                let owner_id = OpaqueId::derive_migration("owner", b"ffi-one-body").unwrap();
                let parent_id = OpaqueId::derive_migration("folder", b"ffi-parent").unwrap();
                let object_id = OpaqueId::derive_migration("diary", b"ffi-object").unwrap();
                let inventory_hash = "a".repeat(64);

                with_python(|py| {
                    let begun = session
                        .preparation_begin_or_resume_v1(
                            py,
                            &keys,
                            &json!({
                                "scope": "pcf004-writing-v1",
                                "expectedValidationGeneration": null,
                                "expectedValidationCatalogSha256": null,
                                "sourceOwnerId": owner_id.as_str(),
                                "sourceSchemaVersion": 1,
                                "sourceMutationGeneration": 1,
                                "sourceInventorySha256": inventory_hash,
                            })
                            .to_string(),
                        )
                        .unwrap();
                    let begun = begun.bind(py).downcast::<PyDict>().unwrap();
                    let pointer_sha256 = begun
                        .get_item("pointerSha256")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap();
                    let snapshot_sequence = begun
                        .get_item("snapshotSequence")
                        .unwrap()
                        .unwrap()
                        .extract::<u64>()
                        .unwrap();
                    let request = json!({
                        "expected": {
                            "pointerSha256": pointer_sha256,
                            "snapshotSequence": snapshot_sequence,
                        },
                        "object": {
                            "objectId": object_id.as_str(),
                            "revision": 1,
                            "objectKeyEpoch": 1,
                            "kind": "diary",
                            "parentId": parent_id.as_str(),
                            "name": "entry.diary.json",
                            "contentType": "application/vnd.anima.diary+json;version=1",
                            "bodyEncoding": "utf-8",
                            "bodyLength": 5,
                            "contentSha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
                            "createdAt": "2026-08-12T00:00:00Z",
                            "updatedAt": "2026-08-12T00:00:00Z",
                            "sourceCharacterCount": 5,
                            "references": [],
                            "policy": "inherit",
                            "stableRole": null,
                            "graphMetadata": {"order": 1},
                            "sourceFingerprintSha256": "c".repeat(64),
                            "converterFormatVersion": 1,
                        },
                    })
                    .to_string();
                    let prepared = session
                        .preparation_prepare_object_v1(
                            py,
                            &keys,
                            &request,
                            PyBuffer::get_bound(&PyBytes::new_bound(py, b"hello")).unwrap(),
                        )
                        .unwrap();
                    let prepared = prepared.bind(py).downcast::<PyDict>().unwrap();
                    let summary_value = prepared.get_item("prepared").unwrap().unwrap();
                    let summary = summary_value.downcast::<PyDict>().unwrap();
                    assert_eq!(
                        prepared
                            .get_item("disposition")
                            .unwrap()
                            .unwrap()
                            .extract::<String>()
                            .unwrap(),
                        "prepared"
                    );
                    assert!(
                        summary
                            .get_item("ciphertextBytes")
                            .unwrap()
                            .unwrap()
                            .extract::<u64>()
                            .unwrap()
                            > 0
                    );
                    for secret in ["physicalName", "wrappedObjectDek", "objectKeyBinding"] {
                        assert!(summary.get_item(secret).unwrap().is_none());
                    }
                    let ordinal = summary
                        .get_item("preparationOrdinal")
                        .unwrap()
                        .unwrap()
                        .extract::<u64>()
                        .unwrap();
                    let page = session
                        .preparation_status_v1(
                            py,
                            &keys,
                            Some(
                                &json!({
                                    "cursorPosition": null,
                                    "maxItems": 10,
                                    "maxBytes": 65536,
                                    "expected": [{
                                        "objectId": object_id.as_str(),
                                        "revision": 1,
                                        "contentSha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
                                        "preparationOrdinal": ordinal,
                                    }],
                                })
                                .to_string(),
                            ),
                        )
                        .unwrap();
                    let page = page.bind(py).downcast::<PyDict>().unwrap();
                    let reconciliation_value = page.get_item("reconciliation").unwrap().unwrap();
                    let reconciliation = reconciliation_value.downcast::<PyDict>().unwrap();
                    assert_eq!(
                        reconciliation
                            .get_item("preparedCount")
                            .unwrap()
                            .unwrap()
                            .extract::<u32>()
                            .unwrap(),
                        1
                    );
                    assert_eq!(
                        reconciliation
                            .get_item("missing")
                            .unwrap()
                            .unwrap()
                            .downcast::<PyList>()
                            .unwrap()
                            .len(),
                        0
                    );
                });
            }

            #[test]
            fn native_error_categories_remain_typed_in_python() {
                with_python(|py| {
                    let conflict = corefs_preparation_error(
                        anima_corefs::transaction::PreparationSessionError::Conflict(
                            "conflict".to_owned(),
                        ),
                    );
                    assert!(conflict.is_instance_of::<CorefsPreparationConflictError>(py));
                    let corrupt = corefs_preparation_error(
                        anima_corefs::transaction::PreparationSessionError::Corruption(
                            "corrupt".to_owned(),
                        ),
                    );
                    assert!(corrupt.is_instance_of::<CorefsPreparationCorruptionError>(py));
                    let source = corefs_preparation_error(
                        anima_corefs::transaction::PreparationSessionError::SourceFence(
                            "source".to_owned(),
                        ),
                    );
                    assert!(source.is_instance_of::<CorefsPreparationSourceFenceError>(py));
                });
            }

            #[test]
            fn python_wrapper_accepts_one_bytes_like_buffer() {
                let root = tempfile::tempdir().unwrap();
                let core_root = root.path().join("bytes-like");
                fs::create_dir_all(&core_root).unwrap();
                let session =
                    super::corefs_session::isolated_session_for_test(&core_root, OBJECT_ID);
                session.begin_close_native();

                with_python(|py| {
                    let session = Py::new(py, session).unwrap();
                    let keys = Py::new(
                        py,
                        PyCorefsSubkeys {
                            inner: derive_corefs_subkeys(
                                &SecretBytes::new(vec![0x74; 32]).unwrap(),
                                1,
                            )
                            .unwrap(),
                        },
                    )
                    .unwrap();
                    let body = PyByteArray::new_bound(py, b"one bytes-like body");
                    let error = session
                        .bind(py)
                        .call_method1("preparation_prepare_object_v1", (keys.bind(py), "{}", body))
                        .unwrap_err();
                    assert!(error.is_instance_of::<pyo3::exceptions::PyRuntimeError>(py));
                });
            }
        }

        mod corefs_session {
            use super::*;

            #[cfg(windows)]
            static PROCESS_HANDLE_PROOF_LOCK: Mutex<()> = Mutex::new(());

            #[cfg(windows)]
            fn process_handle_count() -> u32 {
                use windows_sys::Win32::System::Threading::{
                    GetCurrentProcess, GetProcessHandleCount,
                };

                let mut count = 0;
                // SAFETY: GetCurrentProcess returns a process-local pseudo-handle, and
                // `count` is a valid writable u32 for the duration of the call.
                let succeeded = unsafe { GetProcessHandleCount(GetCurrentProcess(), &mut count) };
                assert_ne!(succeeded, 0, "GetProcessHandleCount failed");
                count
            }

            #[cfg(windows)]
            fn settled_process_handle_count(timeout: Duration) -> u32 {
                let deadline = Instant::now() + timeout;
                let mut last = process_handle_count();
                let mut stable_samples = 0;
                loop {
                    thread::sleep(Duration::from_millis(10));
                    let current = process_handle_count();
                    if current == last {
                        stable_samples += 1;
                        if stable_samples == 5 {
                            return current;
                        }
                    } else {
                        last = current;
                        stable_samples = 0;
                    }
                    assert!(
                        Instant::now() < deadline,
                        "process handle count did not settle before resource proof"
                    );
                }
            }

            #[cfg(windows)]
            fn wait_for_process_handle_baseline(expected: u32, timeout: Duration) -> u32 {
                let deadline = Instant::now() + timeout;
                loop {
                    let current = process_handle_count();
                    if current == expected {
                        return current;
                    }
                    if Instant::now() >= deadline {
                        return current;
                    }
                    thread::sleep(Duration::from_millis(10));
                }
            }

            pub(super) fn isolated_session_for_test(
                core_root: &std::path::Path,
                core_id: &str,
            ) -> PyCorefsSession {
                let coordinator =
                    CoreCommitCoordinator::session_test_new_isolated(core_root, core_id).unwrap();
                PyCorefsSession {
                    canonical_root: coordinator.core_root().to_path_buf(),
                    core_id: core_id.to_owned(),
                    coordinator: Arc::new(coordinator),
                    lifecycle: Arc::new(CorefsSessionLifecycle::default()),
                }
            }

            #[cfg(unix)]
            fn session_with_post_coordinator_hook(
                core_root: &std::path::Path,
                core_id: &str,
                after_coordinator: impl FnOnce(),
            ) -> Result<PyCorefsSession, String> {
                let coordinator = CoreCommitCoordinator::new(core_root, core_id)
                    .map_err(|error| error.to_string())?;
                after_coordinator();
                let canonical_root = coordinator.core_root().to_path_buf();
                Ok(PyCorefsSession {
                    canonical_root,
                    core_id: core_id.to_owned(),
                    coordinator: Arc::new(coordinator),
                    lifecycle: Arc::new(CorefsSessionLifecycle::default()),
                })
            }

            pub(super) fn native_session(
                name: &str,
                core_id: &str,
            ) -> (tempfile::TempDir, Arc<PyCorefsSession>) {
                let root = tempfile::tempdir().unwrap();
                let core_root = root.path().join(name);
                fs::create_dir_all(&core_root).unwrap();
                let session = isolated_session_for_test(&core_root, core_id);
                (root, Arc::new(session))
            }

            #[cfg(windows)]
            fn cached_windows_session(
                name: &str,
            ) -> (
                Arc<LogicalFixture>,
                Arc<PyCorefsSession>,
                WindowsSessionLeaseControl,
            ) {
                let fixture = Arc::new(logical_fixture(name));
                let session = Arc::new(isolated_session_for_test(
                    std::path::Path::new(fixture.root_path()),
                    LOGICAL_CORE_ID,
                ));
                with_python(|py| {
                    session.validation_snapshot(py, &fixture.keys).unwrap();
                });
                let coordinator = session.coordinator_for_test();
                coordinator
                    .session_test_seed_validation_cache(&fixture.keys.inner)
                    .unwrap();
                let control = coordinator.session_test_install_windows_lease().unwrap();
                assert!(session
                    .coordinator_for_test()
                    .session_test_cache_has_object_lease());
                assert_eq!(
                    control.usage(),
                    SessionLeaseUsage {
                        entries: 1,
                        leases: 1,
                        monitor_resources: 3,
                    }
                );
                (fixture, session, control)
            }

            #[cfg(windows)]
            fn fence_then_safe_open_stat(
                session: &PyCorefsSession,
                fixture: &LogicalFixture,
            ) -> PyResult<()> {
                let _operation = session.acquire_operation()?;
                assert_eq!(
                    session
                        .coordinator_for_test()
                        .session_test_fence_cached_lease_is_unknown(),
                    Some(true),
                    "release cancellation must make the blocked real monitor fence Unknown"
                );
                let snapshot = session.open_read_snapshot(
                    &fixture.keys,
                    fixture.selected.head().generation(),
                    fixture.selected.head().catalog_hash(),
                )?;
                snapshot
                    .stat("Notes/Alpha.md")
                    .map_err(corefs_logical_error)?;
                Ok(())
            }

            #[test]
            fn two_calls_in_one_native_session_reuse_one_coordinator() {
                let fixture = logical_fixture("same-session");
                let session =
                    Arc::new(PyCorefsSession::new(fixture.root_path(), LOGICAL_CORE_ID).unwrap());
                let first = session.coordinator_for_test();
                with_python(|py| {
                    session.validation_snapshot(py, &fixture.keys).unwrap();
                    session
                        .stat_v1(
                            py,
                            &fixture.keys,
                            fixture.selected.head().generation(),
                            fixture.selected.head().catalog_hash(),
                            "Notes/Alpha.md",
                        )
                        .unwrap();
                });
                let second = session.coordinator_for_test();
                assert!(Arc::ptr_eq(&first, &second));
            }

            #[test]
            fn validation_converter_bindings_are_session_accounted_and_resolve_roles() {
                let (_root, session) = native_session("validation-converter", "ffi-converter");
                let keys = PyCorefsSubkeys {
                    inner: derive_corefs_subkeys(&SecretBytes::new(vec![0x6a; 32]).unwrap(), 1)
                        .unwrap(),
                };
                let root_id = OpaqueId::derive_migration("folder", b"root").unwrap();
                let journal_id = OpaqueId::derive_migration("folder", b"journal").unwrap();
                let notes_id = OpaqueId::derive_migration("folder", b"notes").unwrap();
                let batch = json!({
                    "initialize": true,
                    "folders": [
                        {"stableId": root_id.as_str(), "parentId": null, "name": "Core", "role": null, "policy": "user-write"},
                        {"stableId": journal_id.as_str(), "parentId": root_id.as_str(), "name": "Journal", "role": "core.journal", "policy": "user-write"},
                        {"stableId": notes_id.as_str(), "parentId": root_id.as_str(), "name": "Notes", "role": "core.notes", "policy": "user-write"}
                    ],
                    "objects": []
                })
                .to_string();

                with_python(|py| {
                    let outcome = session.validation_batch_v1(py, &keys, &batch).unwrap();
                    let outcome = outcome.bind(py).downcast::<PyDict>().unwrap();
                    assert_eq!(
                        outcome
                            .get_item("generation")
                            .unwrap()
                            .unwrap()
                            .extract::<u64>()
                            .unwrap(),
                        1
                    );
                    assert!(outcome
                        .get_item("published")
                        .unwrap()
                        .unwrap()
                        .extract::<bool>()
                        .unwrap());
                    let role = session
                        .resolve_validation_role_v1(py, &keys, "core.journal")
                        .unwrap();
                    let role = role.bind(py).downcast::<PyDict>().unwrap();
                    assert_eq!(
                        role.get_item("stableId")
                            .unwrap()
                            .unwrap()
                            .extract::<String>()
                            .unwrap(),
                        journal_id.as_str()
                    );
                });
                assert_eq!(session.active_operations_for_test(), 0);
            }

            #[test]
            fn validation_converter_rejects_oversized_batches_before_json_decode() {
                let oversized = " ".repeat(anima_corefs::catalog::MAX_CATALOG_PLAINTEXT_SIZE + 1);
                with_python(|_| {
                    let error = decode_validation_batch_json(&oversized).unwrap_err();
                    assert!(error
                        .to_string()
                        .contains("CoreFS catalog limit exceeded: catalog plaintext"));
                });
            }

            #[test]
            fn different_roots_or_core_ids_never_share_a_coordinator() {
                let (_left_root, left) = native_session("left", "core-left");
                let (_right_root, right) = native_session("right", "core-left");
                let (_other_id_root, other_id) = native_session("left", "core-right");
                assert!(!Arc::ptr_eq(
                    &left.coordinator_for_test(),
                    &right.coordinator_for_test()
                ));
                assert!(!Arc::ptr_eq(
                    &left.coordinator_for_test(),
                    &other_id.coordinator_for_test()
                ));
            }

            #[test]
            fn operation_guard_drains_before_close_releases_lease() {
                let (_root, session) = native_session("drain-close", LOGICAL_CORE_ID);
                let guard = session.acquire_operation_for_test().unwrap();
                let close_session = Arc::clone(&session);
                let close_done = Arc::new(AtomicBool::new(false));
                let close_done_thread = Arc::clone(&close_done);
                let closer = thread::spawn(move || {
                    close_session.close_native().unwrap();
                    close_done_thread.store(true, Ordering::SeqCst);
                });
                session.wait_for_phase_for_test(CorefsSessionPhase::Closing);
                assert!(!close_done.load(Ordering::SeqCst));
                drop(guard);
                closer.join().unwrap();
                assert!(close_done.load(Ordering::SeqCst));
                assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
            }

            #[test]
            fn begin_close_terminalizes_before_active_operations_drain() {
                let (_root, session) = native_session("begin-close", LOGICAL_CORE_ID);
                let guard = session.acquire_operation_for_test().unwrap();

                session.begin_close_native();

                assert!(session.acquire_operation_for_test().is_err());
                assert!(session.release_native().is_err());
                drop(guard);
                session.close_native().unwrap();
                assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
            }

            #[test]
            fn release_rejects_new_operations_then_returns_to_open() {
                let (_root, session) = native_session("release-reopen", LOGICAL_CORE_ID);
                let guard = session.acquire_operation_for_test().unwrap();
                let release_session = Arc::clone(&session);
                let releaser = thread::spawn(move || release_session.release_native().unwrap());
                session.wait_for_phase_for_test(CorefsSessionPhase::Releasing);
                assert!(session.acquire_operation_for_test().is_err());
                drop(guard);
                releaser.join().unwrap();
                assert_eq!(session.phase_for_test(), CorefsSessionPhase::Open);
                drop(session.acquire_operation_for_test().unwrap());
            }

            #[test]
            fn close_racing_release_is_terminal_and_never_reopens() {
                #[cfg(windows)]
                {
                    let (_fixture, session, control) = cached_windows_session("release-close-real");
                    let coordinator = session.coordinator_for_test();
                    let mut late_candidate = coordinator
                        .session_test_prepare_windows_candidate()
                        .unwrap();
                    let late_control = late_candidate.control().clone();
                    control.pause_next_native_completion();
                    let release_session = Arc::clone(&session);
                    let releaser = thread::spawn(move || release_session.release_native());
                    assert!(control.wait_until_native_completion_paused(Duration::from_secs(2)));
                    session.wait_for_phase_for_test(CorefsSessionPhase::Releasing);

                    let close_session = Arc::clone(&session);
                    let closer = thread::spawn(move || close_session.close_native());
                    session.wait_for_phase_for_test(CorefsSessionPhase::Closing);
                    assert!(session.acquire_operation_for_test().is_err());
                    control.release_native_completion();

                    assert!(releaser.join().unwrap().is_ok());
                    assert!(closer.join().unwrap().is_ok());
                    assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
                    assert!(
                        coordinator.session_test_cache_is_empty(),
                        "a close racing the release owner must clear all authenticated cache state"
                    );
                    assert_eq!(control.join_count(), 1);
                    assert_eq!(control.native_completion_count(), 1);
                    assert_eq!(
                        control.usage(),
                        SessionLeaseUsage {
                            entries: 1,
                            leases: 1,
                            monitor_resources: 3,
                        }
                    );
                    assert!(
                        !coordinator
                            .session_test_attempt_candidate_publication(&mut late_candidate),
                        "a candidate prepared before terminal close must never publish afterward"
                    );
                    assert!(coordinator.session_test_cache_is_empty());
                    assert_eq!(late_control.join_count(), 1);
                    assert_eq!(late_control.native_completion_count(), 1);
                    assert_eq!(late_control.usage(), SessionLeaseUsage::default());
                }
                #[cfg(not(windows))]
                {
                    let (_root, session) = native_session("release-close", LOGICAL_CORE_ID);
                    let guard = session.acquire_operation_for_test().unwrap();
                    let release_session = Arc::clone(&session);
                    let releaser = thread::spawn(move || release_session.release_native());
                    session.wait_for_phase_for_test(CorefsSessionPhase::Releasing);
                    let close_session = Arc::clone(&session);
                    let closer = thread::spawn(move || close_session.close_native());
                    session.wait_for_phase_for_test(CorefsSessionPhase::Closing);
                    drop(guard);
                    assert!(releaser.join().unwrap().is_ok());
                    assert!(closer.join().unwrap().is_ok());
                    assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
                    assert!(session.acquire_operation_for_test().is_err());
                }
            }

            #[test]
            #[cfg(windows)]
            fn direct_coordinator_release_drains_eligible_late_publisher() {
                let (_fixture, session, _cached_control) =
                    cached_windows_session("direct-release-overlap");
                let coordinator = session.coordinator_for_test();
                let mut late_candidate = coordinator
                    .session_test_prepare_windows_candidate()
                    .unwrap();
                let late_control = late_candidate.control().clone();
                let pause = SessionPublicationPause::default();
                let publisher_coordinator = Arc::clone(&coordinator);
                let publisher_pause = pause.clone();
                let publisher = thread::spawn(move || {
                    publisher_coordinator.session_test_attempt_candidate_publication_paused(
                        &mut late_candidate,
                        &publisher_pause,
                    )
                });
                assert!(pause.wait_until_paused(Duration::from_secs(2)));

                let (release_done_sender, release_done_receiver) = std::sync::mpsc::channel();
                let release_coordinator = Arc::clone(&coordinator);
                let releaser = thread::spawn(move || {
                    release_coordinator.release_object_lease().unwrap();
                    release_done_sender.send(()).unwrap();
                });
                assert!(
                    release_done_receiver
                        .recv_timeout(Duration::from_millis(250))
                        .is_err(),
                    "direct coordinator release returned while an admitted publisher still owned a real candidate"
                );

                pause.release();
                assert!(publisher.join().unwrap());
                releaser.join().unwrap();
                release_done_receiver
                    .recv_timeout(Duration::from_secs(2))
                    .expect("direct coordinator release did not finish after publisher drain");
                assert!(!coordinator.session_test_cache_has_object_lease());
                assert_eq!(late_control.live_monitor_resource_count(), 0);
                assert_eq!(late_control.usage(), SessionLeaseUsage::default());
            }

            #[test]
            fn python_session_maps_reentrant_release_without_mutating_lifecycle() {
                let fixture = logical_fixture("python-reentrant-release");
                let session = Arc::new(isolated_session_for_test(
                    std::path::Path::new(fixture.root_path()),
                    LOGICAL_CORE_ID,
                ));
                let coordinator = session.coordinator_for_test();
                let entries = fixture.selected.catalog().entries().to_vec();
                let (entered_sender, entered_receiver) = std::sync::mpsc::channel();
                let (returned_sender, returned_receiver) = std::sync::mpsc::channel();
                let (completed_sender, completed_receiver) = std::sync::mpsc::channel();
                let worker_session = Arc::clone(&session);
                let worker = thread::spawn(move || {
                    let callback_session = Arc::clone(&worker_session);
                    let outcome = coordinator.commit_first_mutation(
                        &fixture.keys.inner,
                        1,
                        &[],
                        &[],
                        |_, generation| CatalogGeneration::new(generation, entries),
                        |_| {
                            entered_sender.send(()).unwrap();
                            let release = callback_session.release_native().map_err(|error| {
                                let is_value_error = with_python(|py| {
                                    error.is_instance_of::<pyo3::exceptions::PyValueError>(py)
                                });
                                (is_value_error, error.to_string())
                            });
                            returned_sender.send(release).unwrap();
                            Ok(())
                        },
                    );
                    completed_sender
                        .send(
                            outcome
                                .map(|outcome| outcome.generation())
                                .map_err(|error| error.to_string()),
                        )
                        .unwrap();
                });

                entered_receiver
                    .recv_timeout(Duration::from_secs(2))
                    .expect("Python release mapping callback was not reached");
                let release = returned_receiver
                    .recv_timeout(Duration::from_millis(250))
                    .expect("Python session release blocked reentrantly");
                let (is_value_error, message) =
                    release.expect_err("reentrant Python session release unexpectedly succeeded");
                assert!(is_value_error);
                assert!(message.contains("current thread"));
                assert_eq!(session.phase_for_test(), CorefsSessionPhase::Open);
                assert_eq!(
                    completed_receiver
                        .recv_timeout(Duration::from_secs(2))
                        .expect("outer commit did not complete after Python release rejection")
                        .unwrap(),
                    2
                );
                worker.join().unwrap();
                session.release_native().unwrap();
                assert_eq!(session.phase_for_test(), CorefsSessionPhase::Open);
            }

            #[test]
            fn all_features_normal_coordinators_share_process_lease_budget() {
                let first_root = tempfile::tempdir().unwrap();
                let second_root = tempfile::tempdir().unwrap();
                let isolated_root = tempfile::tempdir().unwrap();
                let first = CoreCommitCoordinator::new(first_root.path(), "budget-first").unwrap();
                let second =
                    CoreCommitCoordinator::new(second_root.path(), "budget-second").unwrap();
                let isolated = CoreCommitCoordinator::session_test_new_isolated(
                    isolated_root.path(),
                    "budget-isolated",
                )
                .unwrap();

                let entry_limit = first
                    .session_test_try_reserve_budget(4_096, 0)
                    .expect("normal coordinator should reserve the process entry ceiling");
                assert!(
                    second.session_test_try_reserve_budget(1, 0).is_none(),
                    "normal coordinators must share the 4096-entry process ceiling"
                );
                assert!(isolated.session_test_try_reserve_budget(1, 0).is_some());
                drop(entry_limit);

                let lease_limit = (0..4)
                    .map(|_| {
                        first
                            .session_test_try_reserve_budget(0, 0)
                            .expect("normal coordinator should reserve four process lease slots")
                    })
                    .collect::<Vec<_>>();
                assert!(
                    second.session_test_try_reserve_budget(0, 0).is_none(),
                    "normal coordinators must share the four-lease process ceiling"
                );
                drop(lease_limit);

                let resource_limit = first
                    .session_test_try_reserve_budget(0, 260)
                    .expect("normal coordinator should reserve the process monitor ceiling");
                assert!(
                    second.session_test_try_reserve_budget(0, 1).is_none(),
                    "normal coordinators must share the 260-resource process ceiling"
                );
                drop(resource_limit);
            }

            #[test]
            #[cfg(unix)]
            fn session_root_identity_stays_bound_to_pinned_coordinator_tree() {
                let parent = tempfile::tempdir().unwrap();
                let requested = parent.path().join("requested");
                let pinned = parent.path().join("pinned-tree");
                let replacement = parent.path().join("replacement-tree");
                fs::create_dir_all(&pinned).unwrap();
                fs::create_dir_all(&replacement).unwrap();
                std::os::unix::fs::symlink(&pinned, &requested).unwrap();
                let session = session_with_post_coordinator_hook(&requested, "root-race", || {
                    fs::remove_dir(&requested).unwrap();
                    std::os::unix::fs::symlink(&replacement, &requested).unwrap();
                })
                .unwrap();
                assert_eq!(
                    session.canonical_root,
                    session
                        .coordinator_for_test()
                        .session_test_core_root()
                        .to_path_buf(),
                    "session identity must name the same tree pinned by its coordinator"
                );
            }

            #[test]
            fn session_root_identity_uses_coordinator_canonical_root() {
                let (_root, session) = native_session("root-identity", "root-identity");
                assert_eq!(
                    session.canonical_root,
                    session
                        .coordinator_for_test()
                        .session_test_core_root()
                        .to_path_buf()
                );
            }

            #[test]
            fn two_close_callers_wait_for_closed() {
                let (_root, session) = native_session("two-close", LOGICAL_CORE_ID);
                let guard = session.acquire_operation_for_test().unwrap();
                let rendezvous = Arc::new(Barrier::new(3));
                let completed = Arc::new(AtomicUsize::new(0));
                let callers = (0..2)
                    .map(|_| {
                        let session = Arc::clone(&session);
                        let rendezvous = Arc::clone(&rendezvous);
                        let completed = Arc::clone(&completed);
                        thread::spawn(move || {
                            rendezvous.wait();
                            session.close_native().unwrap();
                            completed.fetch_add(1, Ordering::SeqCst);
                        })
                    })
                    .collect::<Vec<_>>();
                rendezvous.wait();
                session.wait_for_phase_for_test(CorefsSessionPhase::Closing);
                assert_eq!(completed.load(Ordering::SeqCst), 0);
                drop(guard);
                for caller in callers {
                    caller.join().unwrap();
                }
                assert_eq!(completed.load(Ordering::SeqCst), 2);
                assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
            }

            #[test]
            fn guard_drop_on_panic_decrements_active_count() {
                let (_root, session) = native_session("panic-guard", LOGICAL_CORE_ID);
                let result = catch_unwind(AssertUnwindSafe({
                    let session = Arc::clone(&session);
                    move || {
                        let _guard = session.acquire_operation_for_test().unwrap();
                        panic!("injected operation panic");
                    }
                }));
                assert!(result.is_err());
                assert_eq!(session.active_operations_for_test(), 0);
                session.close_native().unwrap();
            }

            #[test]
            fn blocked_fence_cancellation_is_bounded_and_falls_back_unknown() {
                #[cfg(windows)]
                {
                    let (fixture, session, control) = cached_windows_session("blocked-fence-real");
                    let initial_probe_attempts = control.probe_attempt_count();
                    control.pause_next_read();
                    fs::write(
                        session
                            .coordinator_for_test()
                            .objects_path()
                            .join("wake-current-read"),
                        b"wake",
                    )
                    .unwrap();
                    assert!(control.wait_until_read_paused(Duration::from_secs(2)));

                    let operation_session = Arc::clone(&session);
                    let operation_fixture = Arc::clone(&fixture);
                    let operation = thread::spawn(move || {
                        with_python(|_| {
                            fence_then_safe_open_stat(&operation_session, &operation_fixture)
                        })
                    });
                    assert!(control.wait_until_probe_attempt_count(
                        initial_probe_attempts + 1,
                        Duration::from_secs(2)
                    ));

                    let started = Instant::now();
                    let close_session = Arc::clone(&session);
                    let closer = thread::spawn(move || close_session.close_native());
                    assert!(control.wait_until_cancel_requested(Duration::from_secs(2)));
                    control.release_read_pause();
                    assert!(operation.join().unwrap().is_ok());
                    assert!(closer.join().unwrap().is_ok());

                    assert!(started.elapsed() < Duration::from_secs(2));
                    assert!(control.monitor_is_unknown());
                    assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
                    assert_eq!(control.usage(), SessionLeaseUsage::default());
                    assert!(session.coordinator_for_test().session_test_cache_is_empty());
                }
                #[cfg(not(windows))]
                {
                    let (_root, session) = native_session("blocked-fence", LOGICAL_CORE_ID);
                    let guard = session.acquire_operation_for_test().unwrap();
                    let started = Instant::now();
                    let close_session = Arc::clone(&session);
                    let closer = thread::spawn(move || close_session.close_native().unwrap());
                    session.wait_for_phase_for_test(CorefsSessionPhase::Closing);
                    drop(guard);
                    closer.join().unwrap();
                    assert!(started.elapsed() < Duration::from_secs(2));
                    assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
                }
            }

            #[test]
            fn windows_supported_profile_teardown_meets_target() {
                #[cfg(windows)]
                {
                    const HANDLE_PROOF_HELPER: &str =
                        "ANIMA_CORE_NATIVE_SESSION_HANDLE_PROOF_HELPER";
                    if std::env::var_os(HANDLE_PROOF_HELPER).is_none() {
                        let status = std::process::Command::new(std::env::current_exe().unwrap())
                            .arg("--exact")
                            .arg(
                                "ffi::python::tests::corefs_session::windows_supported_profile_teardown_meets_target",
                            )
                            .arg("--nocapture")
                            .env(HANDLE_PROOF_HELPER, "1")
                            .status()
                            .unwrap();
                        assert!(
                            status.success(),
                            "isolated Windows process-handle proof failed"
                        );
                        return;
                    }
                    let _handle_proof = PROCESS_HANDLE_PROOF_LOCK
                        .lock()
                        .unwrap_or_else(|poisoned| poisoned.into_inner());
                    with_python(|_| ());
                    {
                        let (fixture, session, control) =
                            cached_windows_session("windows-target-handle-warmup");
                        session.release_native().unwrap();
                        session.close_native().unwrap();
                        drop(control);
                        drop(session);
                        drop(fixture);
                    }
                    let baseline = settled_process_handle_count(Duration::from_secs(2));
                    {
                        let (fixture, session, control) =
                            cached_windows_session("windows-target-real");
                        let started = Instant::now();
                        session.release_native().unwrap();
                        assert!(started.elapsed() < Duration::from_secs(2));
                        assert!(control.wait_until_cancel_requested(Duration::from_secs(2)));
                        assert_eq!(control.native_completion_count(), 1);
                        assert_eq!(control.join_count(), 1);
                        assert!(!control.native_buffer_alive());
                        assert_eq!(control.live_monitor_resource_count(), 0);
                        assert_eq!(control.usage(), SessionLeaseUsage::default());
                        assert_eq!(session.phase_for_test(), CorefsSessionPhase::Open);
                        session.close_native().unwrap();
                        assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
                        drop(control);
                        drop(session);
                        drop(fixture);
                    }
                    assert_eq!(
                        wait_for_process_handle_baseline(baseline, Duration::from_secs(2)),
                        baseline,
                        "real Windows session create/release/close leaked a process handle"
                    );
                }
                #[cfg(not(windows))]
                {
                    let (_root, session) = native_session("windows-target", LOGICAL_CORE_ID);
                    let started = Instant::now();
                    session.release_native().unwrap();
                    assert!(started.elapsed() < Duration::from_secs(2));
                    assert_eq!(session.phase_for_test(), CorefsSessionPhase::Open);
                }
            }

            #[test]
            fn windows_delayed_native_completion_keeps_session_closing_and_ownership_live() {
                #[cfg(windows)]
                {
                    let (_fixture, session, control) =
                        cached_windows_session("delayed-completion-real");
                    control.pause_next_native_completion();
                    let close_session = Arc::clone(&session);
                    let (done_sender, done_receiver) = std::sync::mpsc::channel();
                    let closer = thread::spawn(move || {
                        let result = close_session.close_native();
                        let _ = done_sender.send(());
                        result
                    });
                    assert!(control.wait_until_native_completion_paused(Duration::from_secs(2)));

                    thread::sleep(Duration::from_millis(2_100));
                    assert!(done_receiver.try_recv().is_err());
                    assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closing);
                    assert!(session.acquire_operation_for_test().is_err());
                    assert_eq!(control.teardown_target_miss_count(), 1);
                    assert_eq!(control.native_completion_count(), 0);
                    assert_eq!(control.join_count(), 0);
                    assert!(control.native_buffer_alive());
                    assert_eq!(control.live_monitor_resource_count(), 3);
                    assert_eq!(
                        control.usage(),
                        SessionLeaseUsage {
                            entries: 1,
                            leases: 1,
                            monitor_resources: 3,
                        }
                    );

                    control.release_native_completion();
                    done_receiver
                        .recv_timeout(Duration::from_secs(2))
                        .expect("session close did not finish after native completion release");
                    assert!(closer.join().unwrap().is_ok());
                    assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
                    assert_eq!(control.native_completion_count(), 1);
                    assert_eq!(control.join_count(), 1);
                    assert!(!control.native_buffer_alive());
                    assert_eq!(control.live_monitor_resource_count(), 0);
                    assert_eq!(control.usage(), SessionLeaseUsage::default());
                }
                #[cfg(not(windows))]
                {
                    let (_root, session) = native_session("delayed-completion", LOGICAL_CORE_ID);
                    session.close_native().unwrap();
                    assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
                }
            }

            #[test]
            fn extended_close_wait_holds_no_gil_or_internal_lock() {
                #[cfg(windows)]
                {
                    let (_fixture, session, control) =
                        cached_windows_session("gil-free-close-real");
                    control.pause_next_native_completion();
                    let close_session = Arc::clone(&session);
                    let closer = thread::spawn(move || with_python(|py| close_session.close(py)));
                    assert!(control.wait_until_native_completion_paused(Duration::from_secs(2)));

                    let gil_probe_started = Instant::now();
                    with_python(|_| ());
                    assert!(gil_probe_started.elapsed() < Duration::from_secs(1));
                    assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closing);
                    let coordinator = session.coordinator_for_test();
                    assert!(coordinator.session_test_cache_lock_available());
                    assert!(coordinator.session_test_budget_lock_available());
                    let lock_probe_deadline = Instant::now() + Duration::from_secs(1);
                    while !control.internal_locks_available() {
                        assert!(
                            Instant::now() < lock_probe_deadline,
                            "native completion wait retained a Windows monitor internal lock"
                        );
                        thread::yield_now();
                    }

                    control.release_native_completion();
                    assert!(closer.join().unwrap().is_ok());
                    assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
                    assert_eq!(control.usage(), SessionLeaseUsage::default());
                }
                #[cfg(not(windows))]
                {
                    let (_root, session) = native_session("gil-free-close", LOGICAL_CORE_ID);
                    with_python(|py| session.close(py).unwrap());
                }
            }

            #[test]
            fn no_resource_or_cache_publication_occurs_after_close_returns() {
                #[cfg(windows)]
                {
                    let (_fixture, session, cached_control) =
                        cached_windows_session("closed-publication-real");
                    let coordinator = session.coordinator_for_test();
                    let mut late_candidate = coordinator
                        .session_test_prepare_windows_candidate()
                        .unwrap();
                    let late_control = late_candidate.control().clone();
                    assert_eq!(
                        late_control.usage(),
                        SessionLeaseUsage {
                            entries: 2,
                            leases: 2,
                            monitor_resources: 6,
                        }
                    );

                    session.close_native().unwrap();
                    assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
                    assert!(session.acquire_operation_for_test().is_err());
                    assert!(session.release_native().is_err());
                    assert!(coordinator.session_test_cache_is_empty());
                    assert_eq!(cached_control.live_monitor_resource_count(), 0);
                    assert_eq!(
                        late_control.usage(),
                        SessionLeaseUsage {
                            entries: 1,
                            leases: 1,
                            monitor_resources: 3,
                        }
                    );

                    assert!(!coordinator
                        .session_test_attempt_candidate_publication(&mut late_candidate));
                    assert!(!coordinator
                        .session_test_attempt_candidate_publication(&mut late_candidate));
                    assert!(coordinator.session_test_cache_is_empty());
                    assert_eq!(late_control.live_monitor_resource_count(), 0);
                    assert_eq!(late_control.native_completion_count(), 1);
                    assert_eq!(late_control.join_count(), 1);
                    assert_eq!(late_control.usage(), SessionLeaseUsage::default());
                    session.close_native().unwrap();
                }
                #[cfg(not(windows))]
                {
                    let (_root, session) = native_session("closed-publication", LOGICAL_CORE_ID);
                    session.close_native().unwrap();
                    assert_eq!(session.phase_for_test(), CorefsSessionPhase::Closed);
                    assert!(session.acquire_operation_for_test().is_err());
                    assert!(session.release_native().is_err());
                    session.close_native().unwrap();
                }
            }
        }

        #[test]
        fn corefs_logical_bindings_use_validation_snapshot_and_model_wire_v1() {
            with_python(|py| {
                let fixture = logical_fixture("read-bindings");
                let selected = corefs_validation_snapshot(
                    py,
                    fixture.root_path(),
                    LOGICAL_CORE_ID,
                    &fixture.keys,
                )
                .unwrap();
                let selected = selected.bind(py).downcast::<PyDict>().unwrap();
                let generation = selected
                    .get_item("generation")
                    .unwrap()
                    .unwrap()
                    .extract::<u64>()
                    .unwrap();
                let catalog_hash = selected
                    .get_item("catalogHash")
                    .unwrap()
                    .unwrap()
                    .extract::<String>()
                    .unwrap();
                assert_eq!(generation, fixture.selected.head().generation());
                assert_eq!(catalog_hash, fixture.selected.head().catalog_hash());

                let stat = corefs_stat_v1(
                    py,
                    fixture.root_path(),
                    LOGICAL_CORE_ID,
                    &fixture.keys,
                    generation,
                    &catalog_hash,
                    "Notes/Alpha.md",
                )
                .unwrap();
                let stat = serde_json::from_slice::<serde_json::Value>(
                    stat.bind(py).downcast::<PyBytes>().unwrap().as_bytes(),
                )
                .unwrap();
                assert_eq!(stat["version"], anima_corefs::logical::MODEL_WIRE_V1);
                assert_eq!(stat["result"]["path"], "Notes/Alpha.md");
                assert_eq!(stat["result"]["stableId"], LOGICAL_OBJECT_ID);
                assert!(stat.to_string().contains("contentHash"));
                assert!(!stat.to_string().contains(&fixture.physical_name));

                let read = corefs_read_chunk_v1(
                    py,
                    fixture.root_path(),
                    LOGICAL_CORE_ID,
                    &fixture.keys,
                    generation,
                    &catalog_hash,
                    "Notes/Alpha.md",
                    0,
                    64,
                    None,
                    None,
                    None,
                    None,
                    None,
                )
                .unwrap()
                .expect("object has one logical read chunk");
                let read = serde_json::from_slice::<serde_json::Value>(
                    read.bind(py).downcast::<PyBytes>().unwrap().as_bytes(),
                )
                .unwrap();
                assert_eq!(read["version"], anima_corefs::logical::MODEL_WIRE_V1);
                assert_eq!(
                    read["result"]["bytesBase64"],
                    "cG9ydGFibGUgbG9naWNhbCBub3Rl"
                );
                assert!(!read.to_string().contains(&fixture.physical_name));

                let stale = corefs_stat_v1(
                    py,
                    fixture.root_path(),
                    LOGICAL_CORE_ID,
                    &fixture.keys,
                    generation + 1,
                    &catalog_hash,
                    "Notes/Alpha.md",
                )
                .unwrap_err();
                assert!(stale
                    .to_string()
                    .contains("validation snapshot no longer matches"));
            });
        }

        #[test]
        fn corefs_public_python_mutators_return_frozen_migration_code() {
            with_python(|py| {
                let args = PyTuple::new_bound(py, ["ignored-path"]);
                let kwargs = PyDict::new_bound(py);
                kwargs.set_item("content", "ignored").unwrap();
                for result in [
                    corefs_mkdir(py, &args, Some(&kwargs)).unwrap(),
                    corefs_create_file(py, &args, Some(&kwargs)).unwrap(),
                    corefs_write_file(py, &args, Some(&kwargs)).unwrap(),
                    corefs_apply_patch(py, &args, Some(&kwargs)).unwrap(),
                    corefs_move(py, &args, Some(&kwargs)).unwrap(),
                    corefs_trash(py, &args, Some(&kwargs)).unwrap(),
                    corefs_restore(py, &args, Some(&kwargs)).unwrap(),
                ] {
                    let result = result.bind(py).downcast::<PyDict>().unwrap();
                    assert!(!result
                        .get_item("ok")
                        .unwrap()
                        .unwrap()
                        .extract::<bool>()
                        .unwrap());
                    assert_eq!(
                        result
                            .get_item("code")
                            .unwrap()
                            .unwrap()
                            .extract::<String>()
                            .unwrap(),
                        anima_corefs::logical::CORE_FS_MIGRATION_WRITE_FROZEN
                    );
                }
            });
        }

        fn logical_fixture(name: &str) -> LogicalFixture {
            let root = tempfile::tempdir().unwrap();
            let core_root = root.path().join(name);
            fs::create_dir_all(&core_root).unwrap();
            let coordinator = CoreCommitCoordinator::new(&core_root, LOGICAL_CORE_ID).unwrap();
            let root_key = SecretBytes::new(vec![0x83; 32]).unwrap();
            let keys = derive_corefs_subkeys(&root_key, 1).unwrap();
            let prepared = prepare_logical_object(
                &coordinator,
                &keys,
                LOGICAL_OBJECT_ID,
                b"portable logical note",
            );
            let physical_name = prepared.physical_name().as_str().to_owned();
            let selected = coordinator
                .initialize_validation_snapshot(
                    &keys,
                    std::slice::from_ref(&prepared),
                    |generation| {
                        CatalogGeneration::new(
                            generation,
                            vec![
                                CatalogGenerationEntry::folder(logical_common(
                                    LOGICAL_ROOT_ID,
                                    None,
                                    "Core",
                                )),
                                CatalogGenerationEntry::folder(logical_common(
                                    LOGICAL_NOTES_ID,
                                    Some(LOGICAL_ROOT_ID),
                                    "Notes",
                                )),
                                CatalogGenerationEntry::object(
                                    logical_common(
                                        LOGICAL_OBJECT_ID,
                                        Some(LOGICAL_NOTES_ID),
                                        "Alpha.md",
                                    ),
                                    CatalogObject::new(
                                        prepared.revision(),
                                        prepared.physical_name().clone(),
                                        prepared.content_hash().clone(),
                                        ObjectKind::Note,
                                        prepared.wrapped_dek().clone(),
                                        ObjectLifecycle::Live,
                                    )
                                    .unwrap(),
                                ),
                            ],
                        )
                    },
                )
                .unwrap();
            LogicalFixture {
                _root: root,
                core_root: core_root
                    .to_str()
                    .expect("tempdir path is utf-8")
                    .to_owned(),
                keys: PyCorefsSubkeys { inner: keys },
                selected,
                physical_name,
            }
        }

        fn prepare_logical_object(
            coordinator: &CoreCommitCoordinator,
            keys: &FrkSubkeys,
            object_id: &str,
            body: &[u8],
        ) -> PreparedObjectRevision {
            let object_key = SecretBytes::new(vec![0x84; 32]).unwrap();
            let aad = ObjectBaseAad::new(
                LOGICAL_CORE_ID,
                object_id,
                ObjectKind::Note,
                ENVELOPE_VERSION,
                1,
                1,
            )
            .unwrap();
            let metadata = EnvelopeMetadata::for_body(
                ObjectKind::Note.as_str(),
                object_id,
                1,
                "2026-07-17T00:00:00Z",
                "2026-07-17T00:00:00Z",
                "text/markdown",
                std::collections::BTreeMap::new(),
                BodyEncoding::Utf8,
                body,
            )
            .unwrap();
            let encoded = encode_envelope(&object_key, &aad, &metadata, body).unwrap();
            coordinator
                .prepare_object_revision(keys, &object_key, &aad, &mut Cursor::new(encoded))
                .unwrap()
        }

        fn logical_common(id: &str, parent_id: Option<&str>, name: &str) -> CatalogEntryCommon {
            CatalogEntryCommon::new(
                OpaqueId::parse(id).unwrap(),
                parent_id.map(|value| OpaqueId::parse(value).unwrap()),
                PortableName::parse(name).unwrap(),
                FolderOwner::User,
                AnimaAccess::Write,
            )
        }

        #[test]
        fn corefs_envelope_and_catalog_bindings_roundtrip_bytes_without_exposing_keys() {
            with_python(|py| {
                let object_dek = PyCorefsObjectDek {
                    inner: anima_corefs::crypto::SecretBytes::new(vec![0x31; 32]).unwrap(),
                };
                let body = b"portable private content";
                let metadata = anima_corefs::envelope::EnvelopeMetadata::for_body(
                    "note",
                    OBJECT_ID,
                    4,
                    "2026-07-15T00:00:00Z",
                    "2026-07-15T00:00:01Z",
                    "text/plain",
                    std::collections::BTreeMap::new(),
                    anima_corefs::envelope::BodyEncoding::Binary,
                    body,
                )
                .unwrap();
                let metadata_json = serde_json::to_vec(&metadata).unwrap();
                let encrypted = corefs_encrypt_object_envelope(
                    py,
                    &object_dek,
                    &metadata_json,
                    body,
                    "01JCORE",
                    OBJECT_ID,
                    4,
                    "note",
                    1,
                    2,
                )
                .unwrap();
                let encrypted_bytes = encrypted.bind(py).downcast::<PyBytes>().unwrap().as_bytes();
                assert!(!encrypted_bytes
                    .windows(body.len())
                    .any(|window| window == body));
                let decrypted = corefs_decrypt_object_envelope(
                    py,
                    &object_dek,
                    encrypted_bytes,
                    "01JCORE",
                    OBJECT_ID,
                    4,
                    "note",
                    1,
                    2,
                )
                .unwrap();
                let result = decrypted.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    result
                        .get_item("body")
                        .unwrap()
                        .unwrap()
                        .downcast::<PyBytes>()
                        .unwrap()
                        .as_bytes(),
                    body
                );
                assert!(result
                    .get_item("whole_body_verified")
                    .unwrap()
                    .unwrap()
                    .extract::<bool>()
                    .unwrap());

                let root = anima_corefs::crypto::SecretBytes::new(vec![0x41; 32]).unwrap();
                let keys = PyCorefsSubkeys {
                    inner: anima_corefs::crypto::derive_corefs_subkeys(&root, 1).unwrap(),
                };
                let payload = anima_corefs::catalog::CatalogPayload::new(
                    3,
                    vec![anima_corefs::catalog::CatalogEntry::new(
                        CATALOG_ID,
                        serde_json::json!({"type": "note"}),
                    )
                    .unwrap()],
                )
                .unwrap();
                let payload_json = anima_corefs::catalog::encode_catalog(&payload).unwrap();
                let encrypted_catalog =
                    corefs_encrypt_catalog(py, &keys, "01JCORE", &payload_json).unwrap();
                let encrypted_catalog_bytes = encrypted_catalog
                    .bind(py)
                    .downcast::<PyBytes>()
                    .unwrap()
                    .as_bytes();
                let decoded =
                    corefs_decrypt_catalog(py, &keys, "01JCORE", encrypted_catalog_bytes).unwrap();
                assert_eq!(
                    decoded.bind(py).downcast::<PyBytes>().unwrap().as_bytes(),
                    payload_json
                );
            });
        }

        #[test]
        fn corefs_streaming_bindings_roundtrip_range_and_enforce_convenience_cap() {
            with_python(|py| {
                let object_dek = PyCorefsObjectDek {
                    inner: anima_corefs::crypto::SecretBytes::new(vec![0x51; 32]).unwrap(),
                };
                let body = b"streamed portable private content";
                let metadata = anima_corefs::envelope::EnvelopeMetadata::for_body(
                    "note",
                    STREAM_ID,
                    5,
                    "2026-07-15T00:00:00Z",
                    "2026-07-15T00:00:01Z",
                    "application/octet-stream",
                    std::collections::BTreeMap::new(),
                    anima_corefs::envelope::BodyEncoding::Binary,
                    body,
                )
                .unwrap();
                let metadata_json = serde_json::to_vec(&metadata).unwrap();
                let bytes_io = py.import_bound("io").unwrap().getattr("BytesIO").unwrap();
                let body_reader = bytes_io.call1((PyBytes::new_bound(py, body),)).unwrap();
                let envelope_writer = bytes_io.call0().unwrap();
                corefs_encrypt_object_envelope_stream(
                    py,
                    &object_dek,
                    &metadata_json,
                    &body_reader,
                    &envelope_writer,
                    "01JCORE",
                    STREAM_ID,
                    5,
                    "note",
                    1,
                    7,
                )
                .unwrap();
                let encrypted = envelope_writer.call_method0("getvalue").unwrap();
                let encrypted = encrypted.downcast::<PyBytes>().unwrap().as_bytes();
                assert!(!encrypted.windows(body.len()).any(|window| window == body));

                let envelope_reader = bytes_io
                    .call1((PyBytes::new_bound(py, encrypted),))
                    .unwrap();
                let body_writer = bytes_io.call0().unwrap();
                let result = corefs_decrypt_object_envelope_stream(
                    py,
                    &object_dek,
                    &envelope_reader,
                    &body_writer,
                    "01JCORE",
                    STREAM_ID,
                    5,
                    "note",
                    1,
                    7,
                )
                .unwrap();
                let result = result.bind(py).downcast::<PyDict>().unwrap();
                assert!(result
                    .get_item("whole_body_verified")
                    .unwrap()
                    .unwrap()
                    .extract::<bool>()
                    .unwrap());
                let streamed_body = body_writer.call_method0("getvalue").unwrap();
                assert_eq!(
                    streamed_body.downcast::<PyBytes>().unwrap().as_bytes(),
                    body
                );

                let envelope_reader = bytes_io
                    .call1((PyBytes::new_bound(py, encrypted),))
                    .unwrap();
                let range_writer = bytes_io.call0().unwrap();
                let range_result = corefs_read_object_envelope_range_stream(
                    py,
                    &object_dek,
                    &envelope_reader,
                    &range_writer,
                    9,
                    17,
                    "01JCORE",
                    STREAM_ID,
                    5,
                    "note",
                    1,
                    7,
                )
                .unwrap();
                let range_result = range_result.bind(py).downcast::<PyDict>().unwrap();
                assert!(!range_result
                    .get_item("whole_body_verified")
                    .unwrap()
                    .unwrap()
                    .extract::<bool>()
                    .unwrap());
                let range_body = range_writer.call_method0("getvalue").unwrap();
                assert_eq!(
                    range_body.downcast::<PyBytes>().unwrap().as_bytes(),
                    &body[9..17]
                );

                let oversized = vec![0_u8; CORE_FS_FFI_IN_MEMORY_LIMIT + 1];
                let error = corefs_encrypt_object_envelope(
                    py,
                    &object_dek,
                    &metadata_json,
                    &oversized,
                    "01JCORE",
                    STREAM_ID,
                    5,
                    "note",
                    1,
                    7,
                )
                .unwrap_err();
                assert!(error.is_instance_of::<pyo3::exceptions::PyValueError>(py));
                assert!(error.to_string().contains("use a streaming CoreFS binding"));

                let closed_reader = bytes_io.call1((PyBytes::new_bound(py, body),)).unwrap();
                closed_reader.call_method0("close").unwrap();
                let unused_writer = bytes_io.call0().unwrap();
                let error = corefs_encrypt_object_envelope_stream(
                    py,
                    &object_dek,
                    &metadata_json,
                    &closed_reader,
                    &unused_writer,
                    "01JCORE",
                    STREAM_ID,
                    5,
                    "note",
                    1,
                    7,
                )
                .unwrap_err();
                assert!(error.is_instance_of::<pyo3::exceptions::PyOSError>(py));
            });
        }

        #[test]
        fn corefs_ffi_rejects_metadata_and_catalog_inputs_before_parsing() {
            with_python(|py| {
                let object_dek = PyCorefsObjectDek {
                    inner: anima_corefs::crypto::SecretBytes::new(vec![0x61; 32]).unwrap(),
                };
                let oversized_metadata =
                    vec![b'['; anima_corefs::envelope::MAX_METADATA_PLAINTEXT_SIZE + 1];
                let error = corefs_encrypt_object_envelope(
                    py,
                    &object_dek,
                    &oversized_metadata,
                    b"",
                    "01JCORE",
                    OBJECT_ID,
                    1,
                    "note",
                    1,
                    1,
                )
                .unwrap_err();
                assert!(error.to_string().contains("metadata plaintext"));

                let bytes_io = py.import_bound("io").unwrap().getattr("BytesIO").unwrap();
                let body_reader = bytes_io.call0().unwrap();
                let envelope_writer = bytes_io.call0().unwrap();
                let error = corefs_encrypt_object_envelope_stream(
                    py,
                    &object_dek,
                    &oversized_metadata,
                    &body_reader,
                    &envelope_writer,
                    "01JCORE",
                    OBJECT_ID,
                    1,
                    "note",
                    1,
                    1,
                )
                .unwrap_err();
                assert!(error.to_string().contains("metadata plaintext"));

                let oversized_catalog =
                    vec![b'['; anima_corefs::catalog::MAX_CATALOG_PLAINTEXT_SIZE + 1];
                let error = corefs_encode_catalog(py, &oversized_catalog).unwrap_err();
                assert!(error.to_string().contains("catalog plaintext"));

                let root = anima_corefs::crypto::SecretBytes::new(vec![0x62; 32]).unwrap();
                let keys = PyCorefsSubkeys {
                    inner: anima_corefs::crypto::derive_corefs_subkeys(&root, 1).unwrap(),
                };
                let payload = anima_corefs::catalog::CatalogPayload::new(
                    1,
                    vec![anima_corefs::catalog::CatalogEntry::new(
                        CATALOG_ID,
                        serde_json::json!({}),
                    )
                    .unwrap()],
                )
                .unwrap();
                let payload = anima_corefs::catalog::encode_catalog(&payload).unwrap();
                let encrypted = corefs_encrypt_catalog(py, &keys, "01JCORE", &payload).unwrap();
                let mut oversized_envelope = encrypted
                    .bind(py)
                    .downcast::<PyBytes>()
                    .unwrap()
                    .as_bytes()
                    .to_vec();
                oversized_envelope.resize(anima_corefs::catalog::MAX_CATALOG_ENVELOPE_SIZE + 1, 0);
                let error = corefs_catalog_physical_name(1, &oversized_envelope).unwrap_err();
                assert!(error.to_string().contains("catalog envelope"));
            });
        }

        #[test]
        fn corefs_streaming_bindings_rollback_partial_outputs_on_late_failures() {
            with_python(|py| {
                let object_dek = PyCorefsObjectDek {
                    inner: anima_corefs::crypto::SecretBytes::new(vec![0x71; 32]).unwrap(),
                };
                let body = b"authenticated body";
                let metadata = anima_corefs::envelope::EnvelopeMetadata::for_body(
                    "note",
                    STREAM_ID,
                    6,
                    "2026-07-15T00:00:00Z",
                    "2026-07-15T00:00:01Z",
                    "application/octet-stream",
                    std::collections::BTreeMap::new(),
                    anima_corefs::envelope::BodyEncoding::Binary,
                    body,
                )
                .unwrap();
                let metadata_json = serde_json::to_vec(&metadata).unwrap();
                let aad = corefs_base_aad("01JCORE", STREAM_ID, 6, "note", 1, 7).unwrap();
                let encoded = anima_corefs::envelope::encode_envelope(
                    &object_dek.inner,
                    &aad,
                    &metadata,
                    body,
                )
                .unwrap();
                let bytes_io = py.import_bound("io").unwrap().getattr("BytesIO").unwrap();
                let prefix = b"existing-prefix";

                let mut too_long = body.to_vec();
                too_long.push(0);
                let body_reader = bytes_io
                    .call1((PyBytes::new_bound(py, &too_long),))
                    .unwrap();
                let envelope_writer = bytes_io.call1((PyBytes::new_bound(py, prefix),)).unwrap();
                envelope_writer.call_method1("seek", (0, 2)).unwrap();
                assert!(corefs_encrypt_object_envelope_stream(
                    py,
                    &object_dek,
                    &metadata_json,
                    &body_reader,
                    &envelope_writer,
                    "01JCORE",
                    STREAM_ID,
                    6,
                    "note",
                    1,
                    7,
                )
                .is_err());
                assert_eq!(
                    envelope_writer
                        .call_method0("getvalue")
                        .unwrap()
                        .downcast::<PyBytes>()
                        .unwrap()
                        .as_bytes(),
                    prefix
                );

                let mut trailing = encoded.clone();
                trailing.push(0);
                let envelope_reader = bytes_io
                    .call1((PyBytes::new_bound(py, &trailing),))
                    .unwrap();
                let body_writer = bytes_io.call1((PyBytes::new_bound(py, prefix),)).unwrap();
                body_writer.call_method1("seek", (0, 2)).unwrap();
                assert!(corefs_decrypt_object_envelope_stream(
                    py,
                    &object_dek,
                    &envelope_reader,
                    &body_writer,
                    "01JCORE",
                    STREAM_ID,
                    6,
                    "note",
                    1,
                    7,
                )
                .is_err());
                assert_eq!(
                    body_writer
                        .call_method0("getvalue")
                        .unwrap()
                        .downcast::<PyBytes>()
                        .unwrap()
                        .as_bytes(),
                    prefix
                );

                let envelope_reader = bytes_io
                    .call1((PyBytes::new_bound(py, &trailing),))
                    .unwrap();
                let range_writer = bytes_io.call1((PyBytes::new_bound(py, prefix),)).unwrap();
                range_writer.call_method1("seek", (0, 2)).unwrap();
                assert!(corefs_read_object_envelope_range_stream(
                    py,
                    &object_dek,
                    &envelope_reader,
                    &range_writer,
                    0,
                    4,
                    "01JCORE",
                    STREAM_ID,
                    6,
                    "note",
                    1,
                    7,
                )
                .is_err());
                assert_eq!(
                    range_writer
                        .call_method0("getvalue")
                        .unwrap()
                        .downcast::<PyBytes>()
                        .unwrap()
                        .as_bytes(),
                    prefix
                );

                let mut wrong_hash = metadata.clone();
                wrong_hash.body_sha256 = "00".repeat(32);
                let mut authenticated_wrong_hash = Vec::new();
                assert!(anima_corefs::envelope::write_envelope(
                    &mut authenticated_wrong_hash,
                    &object_dek.inner,
                    &aad,
                    &wrong_hash,
                    &mut std::io::Cursor::new(body),
                )
                .is_err());
                let envelope_reader = bytes_io
                    .call1((PyBytes::new_bound(py, &authenticated_wrong_hash),))
                    .unwrap();
                let body_writer = bytes_io.call0().unwrap();
                assert!(corefs_decrypt_object_envelope_stream(
                    py,
                    &object_dek,
                    &envelope_reader,
                    &body_writer,
                    "01JCORE",
                    STREAM_ID,
                    6,
                    "note",
                    1,
                    7,
                )
                .is_err());
                assert!(body_writer
                    .call_method0("getvalue")
                    .unwrap()
                    .downcast::<PyBytes>()
                    .unwrap()
                    .as_bytes()
                    .is_empty());

                let envelope_reader = bytes_io.call1((PyBytes::new_bound(py, &encoded),)).unwrap();
                let non_append_writer = bytes_io.call1((PyBytes::new_bound(py, prefix),)).unwrap();
                let error = corefs_decrypt_object_envelope_stream(
                    py,
                    &object_dek,
                    &envelope_reader,
                    &non_append_writer,
                    "01JCORE",
                    STREAM_ID,
                    6,
                    "note",
                    1,
                    7,
                )
                .unwrap_err();
                assert!(error.is_instance_of::<pyo3::exceptions::PyOSError>(py));
                assert_eq!(
                    non_append_writer
                        .call_method0("getvalue")
                        .unwrap()
                        .downcast::<PyBytes>()
                        .unwrap()
                        .as_bytes(),
                    prefix
                );
            });
        }

        #[test]
        fn integrity_report_conversion_exposes_python_friendly_shape() {
            with_python(|py| {
                let mut store = FrameStore::new();
                let id = store.insert(Frame::new(
                    0,
                    FrameKind::Fact,
                    "alpha".into(),
                    "user-1".into(),
                    FrameSource::Api,
                ));
                store.get_mut(id).unwrap().checksum = [9; 32];

                let report = scan_frame_store(&store);
                let obj = integrity_report_to_py_dict(py, &report).unwrap();
                let dict = obj.bind(py).downcast::<PyDict>().unwrap();

                assert!(!dict
                    .get_item("ok")
                    .unwrap()
                    .unwrap()
                    .extract::<bool>()
                    .unwrap());
                assert!(dict.get_item("issues").unwrap().is_some());
                assert!(dict.get_item("stats").unwrap().is_some());

                let issues_obj = dict.get_item("issues").unwrap().unwrap();
                let issues = issues_obj.downcast::<PyList>().unwrap();
                let first_issue_obj = issues.get_item(0).unwrap();
                let first_issue = first_issue_obj.downcast::<PyDict>().unwrap();
                assert_eq!(
                    first_issue
                        .get_item("kind")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "frame_checksum_mismatch"
                );
            });
        }

        #[test]
        fn capsule_report_conversion_exposes_sections_and_issues() {
            with_python(|py| {
                let mut writer = CapsuleWriter::new();
                writer.add_section(SectionKind::Frames, b"frame-data".to_vec());
                let mut capsule = writer.write().unwrap();
                let footer_index = capsule.len() - 1;
                capsule[footer_index] ^= 0xFF;

                let report = verify_capsule_integrity(&capsule, None);
                let obj = capsule_report_to_py_dict(py, &report).unwrap();
                let dict = obj.bind(py).downcast::<PyDict>().unwrap();

                assert!(!dict
                    .get_item("ok")
                    .unwrap()
                    .unwrap()
                    .extract::<bool>()
                    .unwrap());
                assert!(dict.get_item("stats").unwrap().is_some());
                assert!(dict.get_item("capsule").unwrap().is_some());

                let issues_obj = dict.get_item("issues").unwrap().unwrap();
                let issues = issues_obj.downcast::<PyList>().unwrap();
                let first_issue_obj = issues.get_item(0).unwrap();
                let first_issue = first_issue_obj.downcast::<PyDict>().unwrap();
                assert_eq!(
                    first_issue
                        .get_item("kind")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "capsule_footer_checksum_mismatch"
                );
            });
        }

        #[test]
        fn exported_verify_and_stats_functions_accept_py_frame_store() {
            with_python(|py| {
                let mut store = PyFrameStore::new();
                let frame = PyFrame {
                    inner: Frame::new(
                        0,
                        FrameKind::Fact,
                        "alpha".into(),
                        "user-1".into(),
                        FrameSource::Api,
                    ),
                };
                store.insert(&frame);

                let verify_obj = verify_frame_store(py, &store).unwrap();
                let verify_dict = verify_obj.bind(py).downcast::<PyDict>().unwrap();
                assert!(verify_dict.get_item("issues").unwrap().is_some());
                assert!(verify_dict.get_item("stats").unwrap().is_some());

                let stats_obj = frame_store_stats(py, &store).unwrap();
                let stats_dict = stats_obj.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    stats_dict
                        .get_item("frame_count")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    1
                );

                let mut writer = CapsuleWriter::new();
                writer.add_section(SectionKind::Frames, b"frame-data".to_vec());
                let capsule = writer.write().unwrap();

                let capsule_obj = verify_capsule_bytes(py, capsule, None).unwrap();
                let capsule_dict = capsule_obj.bind(py).downcast::<PyDict>().unwrap();
                let capsule_meta_obj = capsule_dict.get_item("capsule").unwrap().unwrap();
                let capsule_meta = capsule_meta_obj.downcast::<PyDict>().unwrap();
                assert!(capsule_meta.get_item("sections").unwrap().is_some());
            });
        }

        #[test]
        fn exported_temporal_range_returns_newest_first_within_bounds() {
            with_python(|_py| {
                let mut store = PyFrameStore::new();
                for (idx, ts) in [1000_i64, 1100, 1200, 1300, 1400].into_iter().enumerate() {
                    let frame = PyFrame {
                        inner: Frame::new(
                            idx as u64,
                            FrameKind::Fact,
                            format!("fact {idx}"),
                            "user-1".into(),
                            FrameSource::Api,
                        )
                        .with_timestamp(ts),
                    };
                    store.insert(&frame);
                }

                let results = store.temporal_range(Some(1100), Some(1300), None);
                let timestamps: Vec<i64> =
                    results.into_iter().map(|frame| frame.timestamp()).collect();
                assert_eq!(timestamps, vec![1300, 1200, 1100]);
            });
        }

        #[test]
        fn exported_temporal_as_of_applies_limit() {
            with_python(|_py| {
                let mut store = PyFrameStore::new();
                for (idx, ts) in [1000_i64, 1100, 1200, 1300, 1400].into_iter().enumerate() {
                    let frame = PyFrame {
                        inner: Frame::new(
                            idx as u64,
                            FrameKind::Fact,
                            format!("fact {idx}"),
                            "user-1".into(),
                            FrameSource::Api,
                        )
                        .with_timestamp(ts),
                    };
                    store.insert(&frame);
                }

                let results = store.temporal_as_of(1250, Some(2));
                let timestamps: Vec<i64> =
                    results.into_iter().map(|frame| frame.timestamp()).collect();
                assert_eq!(timestamps, vec![1200, 1100]);
            });
        }

        #[test]
        fn exported_replay_time_bounds_reports_serialized_session_bounds() {
            with_python(|_py| {
                let session = crate::replay::SerializedSession {
                    session_id: "turn-1".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![crate::replay::ReplayAction {
                        seq: 0,
                        kind: crate::replay::ActionKind::Decision,
                        description: "respond".into(),
                        offset_us: 1_250_000,
                        duration_us: 500_000,
                        frame_ids: vec![],
                        metadata: std::collections::HashMap::new(),
                    }],
                };

                let bytes = serde_json::to_vec(&session).unwrap();
                assert_eq!(
                    replay_session_time_bounds(bytes).unwrap(),
                    Some((1_700_000_000, 1_700_000_002))
                );
            });
        }

        #[test]
        fn exported_temporal_session_window_queries_frames_around_serialized_session() {
            with_python(|_py| {
                let mut store = PyFrameStore::new();
                for (idx, ts) in [1098_i64, 1099, 1100, 1101, 1102, 1103, 1104]
                    .into_iter()
                    .enumerate()
                {
                    let frame = PyFrame {
                        inner: Frame::new(
                            idx as u64,
                            FrameKind::Fact,
                            format!("fact {idx}"),
                            "user-1".into(),
                            FrameSource::Api,
                        )
                        .with_timestamp(ts),
                    };
                    store.insert(&frame);
                }

                let session = crate::replay::SerializedSession {
                    session_id: "turn-1".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1100),
                    actions: vec![crate::replay::ReplayAction {
                        seq: 0,
                        kind: crate::replay::ActionKind::Decision,
                        description: "respond".into(),
                        offset_us: 1_250_000,
                        duration_us: 500_000,
                        frame_ids: vec![],
                        metadata: std::collections::HashMap::new(),
                    }],
                };

                let bytes = serde_json::to_vec(&session).unwrap();
                let results = store.temporal_session_window(bytes, 1, 1, None).unwrap();
                let timestamps: Vec<i64> =
                    results.into_iter().map(|frame| frame.timestamp()).collect();
                assert_eq!(timestamps, vec![1103, 1102, 1101, 1100, 1099]);
            });
        }

        #[test]
        fn exported_temporal_index_snapshots_store_for_reused_queries() {
            with_python(|_py| {
                let mut store = PyFrameStore::new();
                for (idx, ts) in [1000_i64, 1100, 1200].into_iter().enumerate() {
                    let frame = PyFrame {
                        inner: Frame::new(
                            idx as u64,
                            FrameKind::Fact,
                            format!("fact {idx}"),
                            "user-1".into(),
                            FrameSource::Api,
                        )
                        .with_timestamp(ts),
                    };
                    store.insert(&frame);
                }

                let index = store.temporal_index();
                assert_eq!(index.len(), 3);

                let fresh = PyFrame {
                    inner: Frame::new(
                        99,
                        FrameKind::Fact,
                        "fresh".into(),
                        "user-1".into(),
                        FrameSource::Api,
                    )
                    .with_timestamp(1300),
                };
                store.insert(&fresh);

                let cached_range = index.range(Some(1000), Some(1300), None);
                let cached_timestamps: Vec<i64> = cached_range
                    .into_iter()
                    .map(|frame| frame.timestamp())
                    .collect();
                assert_eq!(cached_timestamps, vec![1200, 1100, 1000]);

                let rebuilt_timestamps: Vec<i64> = store
                    .temporal_range(Some(1000), Some(1300), None)
                    .into_iter()
                    .map(|frame| frame.timestamp())
                    .collect();
                assert_eq!(rebuilt_timestamps, vec![1300, 1200, 1100, 1000]);
            });
        }

        #[test]
        fn exported_replay_session_checkpoints_returns_structured_entries() {
            with_python(|py| {
                let session = crate::replay::SerializedSession {
                    session_id: "turn-1".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 250_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "after reflection".into(),
                            offset_us: 1_500_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                };

                let bytes = serde_json::to_vec(&session).unwrap();
                let obj = replay_session_checkpoints(py, bytes).unwrap();
                let checkpoints = obj.bind(py).downcast::<PyList>().unwrap();
                assert_eq!(checkpoints.len(), 2);
                let first_obj = checkpoints.get_item(0).unwrap();
                let first = first_obj.downcast::<PyDict>().unwrap();
                assert_eq!(
                    first
                        .get_item("label")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "before reflection"
                );
                assert_eq!(
                    first
                        .get_item("timestamp")
                        .unwrap()
                        .unwrap()
                        .extract::<i64>()
                        .unwrap(),
                    1_700_000_000
                );
            });
        }

        #[test]
        fn exported_compare_replay_sessions_returns_checkpoint_and_kind_deltas() {
            with_python(|py| {
                let left = crate::replay::SerializedSession {
                    session_id: "left".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 0,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::Reflection,
                            description: "think".into(),
                            offset_us: 0,
                            duration_us: 500_000,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                };
                let right = crate::replay::SerializedSession {
                    session_id: "right".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_100),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 0,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::Decision,
                            description: "respond".into(),
                            offset_us: 0,
                            duration_us: 1_000_000,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 2,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "after decision".into(),
                            offset_us: 1_000_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                };

                let left_bytes = serde_json::to_vec(&left).unwrap();
                let right_bytes = serde_json::to_vec(&right).unwrap();
                let obj = compare_replay_sessions(py, left_bytes, right_bytes).unwrap();
                let comparison = obj.bind(py).downcast::<PyDict>().unwrap();

                assert_eq!(
                    comparison
                        .get_item("action_count_delta")
                        .unwrap()
                        .unwrap()
                        .extract::<i64>()
                        .unwrap(),
                    1
                );
                let shared_obj = comparison
                    .get_item("shared_checkpoint_labels")
                    .unwrap()
                    .unwrap();
                let shared = shared_obj.downcast::<PyList>().unwrap();
                assert_eq!(
                    shared.get_item(0).unwrap().extract::<String>().unwrap(),
                    "before reflection"
                );

                let kind_deltas_obj = comparison.get_item("kind_count_delta").unwrap().unwrap();
                let kind_deltas = kind_deltas_obj.downcast::<PyDict>().unwrap();
                assert_eq!(
                    kind_deltas
                        .get_item("decision")
                        .unwrap()
                        .unwrap()
                        .extract::<i64>()
                        .unwrap(),
                    1
                );
                assert_eq!(
                    kind_deltas
                        .get_item("reflection")
                        .unwrap()
                        .unwrap()
                        .extract::<i64>()
                        .unwrap(),
                    -1
                );
            });
        }

        #[test]
        fn exported_replay_session_summary_returns_structured_totals() {
            with_python(|py| {
                let session = crate::replay::SerializedSession {
                    session_id: "turn-9".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 0,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::MemoryRetrieve,
                            description: "fetch".into(),
                            offset_us: 0,
                            duration_us: 500_000,
                            frame_ids: vec![1, 2],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 2,
                            kind: crate::replay::ActionKind::Decision,
                            description: "respond".into(),
                            offset_us: 1_000_000,
                            duration_us: 1_000_000,
                            frame_ids: vec![2, 3],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 3,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "after decision".into(),
                            offset_us: 2_000_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                };

                let bytes = serde_json::to_vec(&session).unwrap();
                let obj = replay_session_summary(py, bytes).unwrap();
                let summary = obj.bind(py).downcast::<PyDict>().unwrap();

                assert_eq!(
                    summary
                        .get_item("action_count")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    4
                );
                assert_eq!(
                    summary
                        .get_item("checkpoint_count")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    2
                );
                assert_eq!(
                    summary
                        .get_item("referenced_frame_count")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    3
                );
                assert_eq!(
                    summary
                        .get_item("ended_at")
                        .unwrap()
                        .unwrap()
                        .extract::<i64>()
                        .unwrap(),
                    1_700_000_002
                );

                let labels_obj = summary.get_item("checkpoint_labels").unwrap().unwrap();
                let labels = labels_obj.downcast::<PyList>().unwrap();
                assert_eq!(
                    labels.get_item(0).unwrap().extract::<String>().unwrap(),
                    "before reflection"
                );
                assert_eq!(
                    labels.get_item(1).unwrap().extract::<String>().unwrap(),
                    "after decision"
                );

                let kind_counts_obj = summary.get_item("kind_counts").unwrap().unwrap();
                let kind_counts = kind_counts_obj.downcast::<PyDict>().unwrap();
                assert_eq!(
                    kind_counts
                        .get_item("checkpoint")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    2
                );
                assert_eq!(
                    kind_counts
                        .get_item("memory_retrieve")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    1
                );
                assert_eq!(
                    kind_counts
                        .get_item("decision")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    1
                );
            });
        }

        #[test]
        fn exported_replay_session_checkpoint_by_seq_returns_structured_checkpoint() {
            with_python(|py| {
                let session = crate::replay::SerializedSession {
                    session_id: "turn-10".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 250_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::Decision,
                            description: "respond".into(),
                            offset_us: 500_000,
                            duration_us: 500_000,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 2,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "after reflection".into(),
                            offset_us: 1_500_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                };

                let bytes = serde_json::to_vec(&session).unwrap();
                let obj = replay_session_checkpoint_by_seq(py, bytes.clone(), 2)
                    .unwrap()
                    .unwrap();
                let checkpoint = obj.bind(py).downcast::<PyDict>().unwrap();

                assert_eq!(
                    checkpoint
                        .get_item("seq")
                        .unwrap()
                        .unwrap()
                        .extract::<u32>()
                        .unwrap(),
                    2
                );
                assert_eq!(
                    checkpoint
                        .get_item("label")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "after reflection"
                );
                assert_eq!(
                    checkpoint
                        .get_item("timestamp")
                        .unwrap()
                        .unwrap()
                        .extract::<i64>()
                        .unwrap(),
                    1_700_000_001
                );

                assert!(replay_session_checkpoint_by_seq(py, bytes, 99)
                    .unwrap()
                    .is_none());
            });
        }

        #[test]
        fn exported_replay_session_checkpoint_by_label_returns_structured_checkpoint() {
            with_python(|py| {
                let session = crate::replay::SerializedSession {
                    session_id: "turn-11".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 250_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::Reflection,
                            description: "think".into(),
                            offset_us: 500_000,
                            duration_us: 500_000,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                };

                let bytes = serde_json::to_vec(&session).unwrap();
                let obj =
                    replay_session_checkpoint_by_label(py, bytes.clone(), "before reflection")
                        .unwrap()
                        .unwrap();
                let checkpoint = obj.bind(py).downcast::<PyDict>().unwrap();

                assert_eq!(
                    checkpoint
                        .get_item("seq")
                        .unwrap()
                        .unwrap()
                        .extract::<u32>()
                        .unwrap(),
                    0
                );
                assert_eq!(
                    checkpoint
                        .get_item("offset_us")
                        .unwrap()
                        .unwrap()
                        .extract::<u64>()
                        .unwrap(),
                    250_000
                );

                assert!(replay_session_checkpoint_by_label(py, bytes, "missing")
                    .unwrap()
                    .is_none());
            });
        }

        #[test]
        fn exported_replay_registry_summary_and_checkpoint_lookup_are_scoped_by_session_id() {
            with_python(|py| {
                let sessions: Vec<Vec<u8>> = vec![
                    crate::replay::SerializedSession {
                        session_id: "turn-12".into(),
                        user_id: "user-1".into(),
                        started_at: Some(1_700_000_000),
                        actions: vec![
                            crate::replay::ReplayAction {
                                seq: 0,
                                kind: crate::replay::ActionKind::Checkpoint,
                                description: "before reflection".into(),
                                offset_us: 100_000,
                                duration_us: 0,
                                frame_ids: vec![],
                                metadata: std::collections::HashMap::new(),
                            },
                            crate::replay::ReplayAction {
                                seq: 1,
                                kind: crate::replay::ActionKind::Decision,
                                description: "respond".into(),
                                offset_us: 500_000,
                                duration_us: 500_000,
                                frame_ids: vec![1],
                                metadata: std::collections::HashMap::new(),
                            },
                        ],
                    },
                    crate::replay::SerializedSession {
                        session_id: "turn-13".into(),
                        user_id: "user-2".into(),
                        started_at: Some(1_700_000_100),
                        actions: vec![crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "after tool".into(),
                            offset_us: 1_000_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        }],
                    },
                ]
                .into_iter()
                .map(|session| serde_json::to_vec(&session).unwrap())
                .collect();

                let summary_obj = replay_registry_session_summary(py, sessions.clone(), "turn-12")
                    .unwrap()
                    .unwrap();
                let summary = summary_obj.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    summary
                        .get_item("action_count")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    2
                );

                let checkpoint_obj = replay_registry_checkpoint_by_label(
                    py,
                    sessions.clone(),
                    "turn-13",
                    "after tool",
                )
                .unwrap()
                .unwrap();
                let checkpoint = checkpoint_obj.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    checkpoint
                        .get_item("seq")
                        .unwrap()
                        .unwrap()
                        .extract::<u32>()
                        .unwrap(),
                    0
                );
                assert_eq!(
                    checkpoint
                        .get_item("timestamp")
                        .unwrap()
                        .unwrap()
                        .extract::<i64>()
                        .unwrap(),
                    1_700_000_101
                );

                assert!(
                    replay_registry_session_summary(py, sessions.clone(), "missing")
                        .unwrap()
                        .is_none()
                );
                assert!(
                    replay_registry_checkpoint_by_label(py, sessions, "turn-12", "missing")
                        .unwrap()
                        .is_none()
                );
            });
        }

        #[test]
        fn exported_replay_registry_rejects_duplicate_session_ids() {
            with_python(|py| {
                let duplicate_sessions: Vec<Vec<u8>> = vec![
                    crate::replay::SerializedSession {
                        session_id: "turn-14".into(),
                        user_id: "user-1".into(),
                        started_at: Some(1_700_000_000),
                        actions: vec![],
                    },
                    crate::replay::SerializedSession {
                        session_id: "turn-14".into(),
                        user_id: "user-2".into(),
                        started_at: Some(1_700_000_100),
                        actions: vec![],
                    },
                ]
                .into_iter()
                .map(|session| serde_json::to_vec(&session).unwrap())
                .collect();

                let err =
                    replay_registry_session_summary(py, duplicate_sessions, "turn-14").unwrap_err();
                assert!(err.is_instance_of::<pyo3::exceptions::PyValueError>(py));
                assert!(err
                    .to_string()
                    .contains("duplicate replay session id: turn-14"));
            });
        }

        #[test]
        fn exported_replay_registry_session_ids_are_sorted() {
            with_python(|_py| {
                let sessions: Vec<Vec<u8>> = vec![
                    crate::replay::SerializedSession {
                        session_id: "turn-b".into(),
                        user_id: "user-2".into(),
                        started_at: Some(1_700_000_100),
                        actions: vec![],
                    },
                    crate::replay::SerializedSession {
                        session_id: "turn-a".into(),
                        user_id: "user-1".into(),
                        started_at: Some(1_700_000_000),
                        actions: vec![],
                    },
                ]
                .into_iter()
                .map(|session| serde_json::to_vec(&session).unwrap())
                .collect();

                let ids = replay_registry_session_ids(sessions).unwrap();
                assert_eq!(ids, vec!["turn-a".to_string(), "turn-b".to_string()]);
            });
        }

        #[test]
        fn exported_replay_registry_checkpoint_by_seq_returns_structured_checkpoint() {
            with_python(|py| {
                let sessions: Vec<Vec<u8>> = vec![crate::replay::SerializedSession {
                    session_id: "turn-16".into(),
                    user_id: "user-1".into(),
                    started_at: Some(1_700_000_000),
                    actions: vec![
                        crate::replay::ReplayAction {
                            seq: 0,
                            kind: crate::replay::ActionKind::Checkpoint,
                            description: "before reflection".into(),
                            offset_us: 250_000,
                            duration_us: 0,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                        crate::replay::ReplayAction {
                            seq: 1,
                            kind: crate::replay::ActionKind::Decision,
                            description: "respond".into(),
                            offset_us: 500_000,
                            duration_us: 500_000,
                            frame_ids: vec![],
                            metadata: std::collections::HashMap::new(),
                        },
                    ],
                }]
                .into_iter()
                .map(|session| serde_json::to_vec(&session).unwrap())
                .collect();

                let checkpoint =
                    replay_registry_checkpoint_by_seq(py, sessions.clone(), "turn-16", 0)
                        .unwrap()
                        .unwrap();
                let checkpoint = checkpoint.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    checkpoint
                        .get_item("label")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "before reflection"
                );

                assert!(
                    replay_registry_checkpoint_by_seq(py, sessions, "turn-16", 99)
                        .unwrap()
                        .is_none()
                );
            });
        }

        #[test]
        fn capsule_verify_binding_uses_integrity_issue_kinds() {
            with_python(|py| {
                let mut writer = CapsuleWriter::new();
                writer.add_section(SectionKind::Frames, b"frame-data".to_vec());
                let mut capsule = writer.write().unwrap();
                let footer_index = capsule.len() - 1;
                capsule[footer_index] ^= 0xFF;

                let capsule_obj = verify_capsule_bytes(py, capsule, None).unwrap();
                let capsule_dict = capsule_obj.bind(py).downcast::<PyDict>().unwrap();
                let issues_obj = capsule_dict.get_item("issues").unwrap().unwrap();
                let issues = issues_obj.downcast::<PyList>().unwrap();
                let first_issue_obj = issues.get_item(0).unwrap();
                let first_issue = first_issue_obj.downcast::<PyDict>().unwrap();
                let kind = first_issue
                    .get_item("kind")
                    .unwrap()
                    .unwrap()
                    .extract::<String>()
                    .unwrap();

                assert_eq!(
                    kind,
                    serde_json::to_value(IntegrityIssueKind::CapsuleFooterChecksumMismatch)
                        .unwrap()
                        .as_str()
                        .unwrap()
                );
            });
        }

        fn make_memory_card(
            entity: &str,
            slot: &str,
            value: &str,
            version: VersionRelation,
            frame_id: u64,
            created_at: i64,
        ) -> MemoryCard {
            MemoryCard {
                id: 0,
                kind: MemoryKind::Fact,
                entity: entity.into(),
                slot: slot.into(),
                value: value.into(),
                polarity: Polarity::Neutral,
                version,
                confidence: 1.0,
                frame_id,
                created_at,
                active: true,
                superseded_by: None,
            }
        }

        #[test]
        fn exported_entity_state_returns_python_friendly_shape() {
            with_python(|py| {
                let mut cards = PyCardStore::new();
                cards.inner.put(make_memory_card(
                    "user",
                    "likes",
                    "coffee",
                    VersionRelation::Sets,
                    10,
                    300,
                ));
                cards.inner.put(make_memory_card(
                    "user",
                    "likes",
                    "alpha",
                    VersionRelation::Extends,
                    20,
                    100,
                ));

                let mut graph = PyKnowledgeGraph::new();
                let user = graph
                    .inner
                    .upsert_node("user", EntityKind::Person, 0.9, 1)
                    .unwrap();
                let openai = graph
                    .inner
                    .upsert_node("OpenAI", EntityKind::Organization, 0.9, 2)
                    .unwrap();
                graph
                    .inner
                    .upsert_edge(user, openai, "employer", 0.9, 99)
                    .unwrap();

                let obj = project_entity_state(py, &cards, &graph, "user").unwrap();
                let dict = obj.bind(py).downcast::<PyDict>().unwrap();

                assert_eq!(
                    dict.get_item("entity")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "user"
                );
                assert!(dict.get_item("slots").unwrap().is_some());
                assert!(dict.get_item("connected_entities").unwrap().is_some());
                assert!(dict.get_item("supporting_frame_ids").unwrap().is_some());

                let slots_value = dict.get_item("slots").unwrap().unwrap();
                let slots = slots_value.downcast::<PyList>().unwrap();
                assert_eq!(slots.len(), 1);
                let slot_value = slots.get_item(0).unwrap();
                let slot = slot_value.downcast::<PyDict>().unwrap();
                assert_eq!(
                    slot.get_item("slot")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "likes"
                );
                let values_binding = slot.get_item("values").unwrap().unwrap();
                let values = values_binding.downcast::<PyList>().unwrap();
                assert_eq!(
                    values
                        .iter()
                        .map(|item| item.extract::<String>().unwrap())
                        .collect::<Vec<_>>(),
                    vec!["alpha".to_string(), "coffee".to_string()]
                );
                let slot_supporting_binding =
                    slot.get_item("supporting_frame_ids").unwrap().unwrap();
                let slot_supporting = slot_supporting_binding.downcast::<PyList>().unwrap();
                assert_eq!(
                    slot_supporting
                        .iter()
                        .map(|item| item.extract::<u64>().unwrap())
                        .collect::<Vec<_>>(),
                    vec![10, 20]
                );

                let connected_value = dict.get_item("connected_entities").unwrap().unwrap();
                let connected = connected_value.downcast::<PyList>().unwrap();
                assert_eq!(connected.len(), 1);
                let neighbor_value = connected.get_item(0).unwrap();
                let neighbor = neighbor_value.downcast::<PyDict>().unwrap();
                assert_eq!(
                    neighbor
                        .get_item("relation_type")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "employer"
                );
                assert_eq!(
                    neighbor
                        .get_item("entity_name")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "OpenAI"
                );
                assert_eq!(
                    neighbor
                        .get_item("entity_kind")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "organization"
                );
                assert_eq!(
                    neighbor
                        .get_item("supporting_frame_ids")
                        .unwrap()
                        .unwrap()
                        .downcast::<PyList>()
                        .unwrap()
                        .iter()
                        .map(|item| item.extract::<u64>().unwrap())
                        .collect::<Vec<_>>(),
                    vec![99]
                );

                let supporting_binding = dict.get_item("supporting_frame_ids").unwrap().unwrap();
                let supporting = supporting_binding.downcast::<PyList>().unwrap();
                assert_eq!(
                    supporting
                        .iter()
                        .map(|item| item.extract::<u64>().unwrap())
                        .collect::<Vec<_>>(),
                    vec![10, 20, 99]
                );
            });
        }

        #[test]
        fn exported_slot_history_returns_ordered_versions() {
            with_python(|py| {
                let mut cards = PyCardStore::new();
                cards.inner.put(make_memory_card(
                    "user",
                    "employer",
                    "Google",
                    VersionRelation::Sets,
                    1,
                    300,
                ));
                cards.inner.put(make_memory_card(
                    "user",
                    "employer",
                    "Meta",
                    VersionRelation::Updates,
                    2,
                    100,
                ));
                cards.inner.put(make_memory_card(
                    "user",
                    "employer",
                    "Meta",
                    VersionRelation::Retracts,
                    3,
                    200,
                ));

                let obj = project_slot_history(py, &cards, "user", "employer").unwrap();
                let history = obj.bind(py).downcast::<PyList>().unwrap();

                assert_eq!(history.len(), 3);
                let first_value = history.get_item(0).unwrap();
                let second_value = history.get_item(1).unwrap();
                let third_value = history.get_item(2).unwrap();
                let first = first_value.downcast::<PyDict>().unwrap();
                let second = second_value.downcast::<PyDict>().unwrap();
                let third = third_value.downcast::<PyDict>().unwrap();

                assert_eq!(
                    first
                        .get_item("value")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "Google"
                );
                assert_eq!(
                    first
                        .get_item("version")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "sets"
                );
                assert_eq!(
                    second
                        .get_item("value")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "Meta"
                );
                assert_eq!(
                    second
                        .get_item("version")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "updates"
                );
                assert_eq!(
                    third
                        .get_item("value")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "Meta"
                );
                assert_eq!(
                    third
                        .get_item("version")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "retracts"
                );
            });
        }

        #[test]
        fn exported_engine_class_supports_verify_project_and_temporal_queries() {
            with_python(|py| {
                let mut frames = FrameStore::new();
                let mut cards = CardStore::new(SchemaRegistry::new());
                let mut graph = KnowledgeGraph::new();

                let older_frame_id = frames.insert(
                    Frame::new(
                        0,
                        FrameKind::Fact,
                        "user worked at Google".into(),
                        "user".into(),
                        FrameSource::Api,
                    )
                    .with_timestamp(1_700_000_000),
                );
                let newer_frame_id = frames.insert(
                    Frame::new(
                        0,
                        FrameKind::Fact,
                        "user works at OpenAI".into(),
                        "user".into(),
                        FrameSource::Api,
                    )
                    .with_timestamp(1_700_000_100),
                );

                cards.put(make_memory_card(
                    "user",
                    "employer",
                    "Google",
                    VersionRelation::Sets,
                    older_frame_id,
                    100,
                ));
                cards.put(make_memory_card(
                    "user",
                    "employer",
                    "OpenAI",
                    VersionRelation::Updates,
                    newer_frame_id,
                    200,
                ));

                graph
                    .upsert_node("user", EntityKind::Person, 1.0, newer_frame_id)
                    .unwrap();
                graph
                    .upsert_node("OpenAI", EntityKind::Organization, 1.0, newer_frame_id)
                    .unwrap();
                let from = graph.get_by_name("user").unwrap().id;
                let to = graph.get_by_name("OpenAI").unwrap().id;
                graph
                    .upsert_edge(from, to, "employer", 1.0, newer_frame_id)
                    .unwrap();

                let engine = PyAnimaEngine {
                    inner: crate::engine::AnimaEngine::from_parts(frames, cards, graph),
                };

                let verify_obj = engine.verify(py).unwrap();
                let verify = verify_obj.bind(py).downcast::<PyDict>().unwrap();
                assert!(!verify
                    .get_item("ok")
                    .unwrap()
                    .unwrap()
                    .extract::<bool>()
                    .unwrap());

                let stats_obj = engine.stats(py).unwrap();
                let stats = stats_obj.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    stats
                        .get_item("frame_count")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    2
                );
                assert_eq!(
                    stats
                        .get_item("graph_edge_count")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    1
                );

                let state_obj = engine.project_entity_state(py, "user").unwrap();
                let state = state_obj.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    state
                        .get_item("entity")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "user"
                );
                let slots_value = state.get_item("slots").unwrap().unwrap();
                let slots = slots_value.downcast::<PyList>().unwrap();
                assert_eq!(slots.len(), 1);
                let slot_value = slots.get_item(0).unwrap();
                let slot = slot_value.downcast::<PyDict>().unwrap();
                assert_eq!(
                    slot.get_item("slot")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "employer"
                );

                let history_obj = engine.project_slot_history(py, "user", "employer").unwrap();
                let history = history_obj.bind(py).downcast::<PyList>().unwrap();
                assert_eq!(history.len(), 2);
                assert_eq!(
                    history
                        .get_item(0)
                        .unwrap()
                        .downcast::<PyDict>()
                        .unwrap()
                        .get_item("value")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "Google"
                );
                assert_eq!(
                    history
                        .get_item(1)
                        .unwrap()
                        .downcast::<PyDict>()
                        .unwrap()
                        .get_item("value")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "OpenAI"
                );

                let temporal_obj = engine
                    .temporal_range(py, Some(1_700_000_000), Some(1_700_000_100), Some(2))
                    .unwrap();
                let temporal = temporal_obj.bind(py).downcast::<PyList>().unwrap();
                assert_eq!(temporal.len(), 2);
                assert_eq!(
                    temporal
                        .get_item(0)
                        .unwrap()
                        .downcast::<PyDict>()
                        .unwrap()
                        .get_item("content")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "user works at OpenAI"
                );
                assert_eq!(
                    temporal
                        .get_item(1)
                        .unwrap()
                        .downcast::<PyDict>()
                        .unwrap()
                        .get_item("content")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "user worked at Google"
                );
            });
        }

        #[test]
        fn exported_engine_capsule_roundtrip_restores_state() {
            with_python(|py| {
                let mut frames = FrameStore::new();
                let mut cards = CardStore::new(SchemaRegistry::new());
                let mut graph = KnowledgeGraph::new();

                let frame_id = frames.insert(
                    Frame::new(
                        0,
                        FrameKind::Fact,
                        "user works at OpenAI".into(),
                        "user".into(),
                        FrameSource::Api,
                    )
                    .with_timestamp(1_700_000_200),
                );

                cards.put(make_memory_card(
                    "user",
                    "employer",
                    "OpenAI",
                    VersionRelation::Sets,
                    frame_id,
                    200,
                ));

                graph
                    .upsert_node("user", EntityKind::Person, 1.0, frame_id)
                    .unwrap();
                graph
                    .upsert_node("OpenAI", EntityKind::Organization, 1.0, frame_id)
                    .unwrap();
                let from = graph.get_by_name("user").unwrap().id;
                let to = graph.get_by_name("OpenAI").unwrap().id;
                graph
                    .upsert_edge(from, to, "employer", 1.0, frame_id)
                    .unwrap();

                let engine = PyAnimaEngine {
                    inner: crate::engine::AnimaEngine::from_parts(frames, cards, graph),
                };
                let capsule = engine.to_capsule_bytes(None).unwrap();
                let restored = PyAnimaEngine::from_capsule_bytes(capsule, None).unwrap();

                let stats_obj = restored.stats(py).unwrap();
                let stats = stats_obj.bind(py).downcast::<PyDict>().unwrap();
                assert_eq!(
                    stats
                        .get_item("frame_count")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    1
                );
                assert_eq!(
                    stats
                        .get_item("card_count")
                        .unwrap()
                        .unwrap()
                        .extract::<usize>()
                        .unwrap(),
                    1
                );

                let state_obj = restored.project_entity_state(py, "user").unwrap();
                let state = state_obj.bind(py).downcast::<PyDict>().unwrap();
                let slots_value = state.get_item("slots").unwrap().unwrap();
                let slots = slots_value.downcast::<PyList>().unwrap();
                assert_eq!(slots.len(), 1);
                let slot_item = slots.get_item(0).unwrap();
                let slot_value = slot_item.downcast::<PyDict>().unwrap();
                let values_value = slot_value.get_item("values").unwrap().unwrap();
                let values = values_value.downcast::<PyList>().unwrap();
                assert_eq!(
                    values
                        .iter()
                        .map(|item: pyo3::Bound<'_, pyo3::PyAny>| item.extract::<String>().unwrap())
                        .collect::<Vec<_>>(),
                    vec!["OpenAI".to_string()]
                );

                let temporal_obj = restored.temporal_range(py, None, None, Some(1)).unwrap();
                let temporal = temporal_obj.bind(py).downcast::<PyList>().unwrap();
                assert_eq!(temporal.len(), 1);
                assert_eq!(
                    temporal
                        .get_item(0)
                        .unwrap()
                        .downcast::<PyDict>()
                        .unwrap()
                        .get_item("content")
                        .unwrap()
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                    "user works at OpenAI"
                );
            });
        }

        #[test]
        fn python_bindings_reject_invalid_enum_strings() {
            with_python(|_py| {
                assert!(PyFrame::new("not-a-kind", "alpha".into(), "user-1".into()).is_err());

                let mut cards = PyCardStore::new();
                assert!(cards
                    .put("user", "likes", "coffee", "bogus", "sets", 1.0, 0)
                    .is_err());
                assert!(cards
                    .put("user", "likes", "coffee", "fact", "bogus", 1.0, 0)
                    .is_err());

                let mut graph = PyKnowledgeGraph::new();
                assert!(graph.upsert_node("alice", "bogus", 1.0, 0).is_err());

                let mut rules = PyRulesEngine::new(false);
                assert!(rules
                    .add_rule("r1", ".*", "bogus", "user", "slot", "value")
                    .is_err());
            });
        }
    }
}

#[cfg(test)]
mod corefs_preparation_contract {
    const FFI_SOURCE: &str = include_str!("ffi.rs");

    fn session_methods() -> &'static str {
        FFI_SOURCE
            .split("impl PyCorefsSession {")
            .nth(2)
            .expect("PyCorefsSession pymethods impl must exist")
            .split("#[pyclass(name = \"CorefsSubkeys\")]")
            .next()
            .expect("CorefsSubkeys must follow session methods")
    }

    #[test]
    fn exposes_only_the_versioned_bounded_preparation_surface() {
        for method in [
            "fn preparation_begin_or_resume_v1(",
            "fn preparation_status_v1(",
            "fn preparation_prepare_object_v1(",
            "fn preparation_seal_v1(",
            "fn preparation_finalize_v1(",
            "fn preparation_abandon_v1(",
            "fn preparation_quarantine_corrupt_pointer_v1(",
        ] {
            assert!(session_methods().contains(method), "missing {method}");
        }
    }

    #[test]
    fn one_object_method_has_no_aggregate_body_transport() {
        let method = session_methods()
            .split("fn preparation_prepare_object_v1(")
            .nth(1)
            .expect("preparation_prepare_object_v1 must exist")
            .split("\n        fn preparation_seal_v1(")
            .next()
            .expect("preparation_seal_v1 must follow prepare_object");
        assert!(method.contains("body: PyBuffer<u8>"));
        for forbidden in [
            "Vec<Vec<u8>>",
            "Vec<PyBytes",
            "content_parts",
            "contentBodies",
        ] {
            assert!(
                !method.contains(forbidden),
                "one-object preparation accepted forbidden aggregate transport {forbidden}"
            );
        }
    }
}
