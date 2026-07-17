use std::collections::BTreeMap;
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};

use anima_file_tools::{
    BackendCapabilities, BackendKind, BackendPath, DirectoryEntry, DirectoryListing, EntryKind,
    EntryMetadata, FileBackend, FileToolError, MutationAtomicity, PatchError, PatchPlan,
    PatchSnapshot, PathSemantics, PlannedMutation, ReadBackend, ReadSeek, WalkBackend,
    MAX_RESPONSE_BYTES, MAX_WALK_ENTRIES,
};

use crate::permissions::{PermissionDecision, PermissionPolicy};

#[derive(Clone)]
pub(super) struct HostFsBackend {
    policy: PermissionPolicy,
    case_sensitive_paths: bool,
}

impl HostFsBackend {
    pub(super) fn new(policy: PermissionPolicy) -> Self {
        let case_sensitive_paths = host_paths_are_case_sensitive(policy.workspace());
        Self {
            policy,
            case_sensitive_paths,
        }
    }

    #[cfg(test)]
    fn with_case_sensitivity(policy: PermissionPolicy, case_sensitive_paths: bool) -> Self {
        Self {
            policy,
            case_sensitive_paths,
        }
    }

    pub(super) fn resolve_read(&self, path: &str) -> Result<PathBuf, FileToolError> {
        reject_cross_backend_path(path)?;
        match self.policy.check_file_read(PathBuf::from(path)) {
            PermissionDecision::Allow => {
                self.policy
                    .normalize_workspace_path(path)
                    .map_err(|reason| FileToolError::InvalidPath {
                        path: path.to_string(),
                        reason,
                    })
            }
            PermissionDecision::Ask { reason } | PermissionDecision::Deny { reason } => {
                Err(FileToolError::InvalidPath {
                    path: path.to_string(),
                    reason,
                })
            }
        }
    }

    pub(super) fn resolve_write(&self, path: &str) -> Result<PathBuf, FileToolError> {
        reject_cross_backend_path(path)?;
        match self.policy.check_file_write(PathBuf::from(path)) {
            PermissionDecision::Allow => {
                self.policy
                    .normalize_workspace_path(path)
                    .map_err(|reason| FileToolError::InvalidPath {
                        path: path.to_string(),
                        reason,
                    })
            }
            PermissionDecision::Ask { reason } | PermissionDecision::Deny { reason } => {
                Err(FileToolError::InvalidPath {
                    path: path.to_string(),
                    reason,
                })
            }
        }
    }

    fn resolve_read_entry(&self, path: &str) -> Result<PathBuf, FileToolError> {
        let resolved = self.resolve_read(path)?;
        let raw = PathBuf::from(path);
        let requested = if raw.is_absolute() {
            raw
        } else {
            self.policy.workspace().join(raw)
        };
        let requested_metadata = fs::symlink_metadata(&requested)
            .map_err(|error| backend_error("metadata", &requested, error))?;
        if !requested_metadata.file_type().is_symlink() {
            return Ok(resolved);
        }
        let name = requested
            .file_name()
            .ok_or_else(|| FileToolError::InvalidPath {
                path: path.to_string(),
                reason: "path must name a workspace entry".to_string(),
            })?;
        let parent = requested.parent().unwrap_or_else(|| Path::new(""));
        self.policy
            .normalize_workspace_path(parent)
            .map(|resolved_parent| resolved_parent.join(name))
            .map_err(|reason| FileToolError::InvalidPath {
                path: path.to_string(),
                reason,
            })
    }

    fn resolve_entry_write(&self, path: &str) -> Result<PathBuf, FileToolError> {
        reject_cross_backend_path(path)?;
        let raw = PathBuf::from(path);
        let name = raw.file_name().ok_or_else(|| FileToolError::InvalidPath {
            path: path.to_string(),
            reason: "path must name a workspace entry".to_string(),
        })?;
        let parent = raw.parent().unwrap_or_else(|| Path::new(""));
        match self.policy.check_file_write(parent) {
            PermissionDecision::Allow => self
                .policy
                .normalize_workspace_path(parent)
                .map(|resolved_parent| resolved_parent.join(name))
                .map_err(|reason| FileToolError::InvalidPath {
                    path: path.to_string(),
                    reason,
                }),
            PermissionDecision::Ask { reason } | PermissionDecision::Deny { reason } => {
                Err(FileToolError::InvalidPath {
                    path: path.to_string(),
                    reason,
                })
            }
        }
    }

    pub(super) fn write_text(&self, path: &str, content: &str) -> Result<PathBuf, FileToolError> {
        validate_write_size(content)?;
        let resolved = self.resolve_write(path)?;
        if let Some(parent) = resolved.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| backend_error("create_directory", parent, error))?;
        }
        fs::write(&resolved, content)
            .map_err(|error| backend_error("write_text", &resolved, error))?;
        Ok(resolved)
    }

    pub(super) fn apply_plan(&self, plan: &PatchPlan) -> Result<(), FileToolError> {
        let resolved = plan
            .mutations
            .iter()
            .map(|mutation| self.resolve_mutation(mutation))
            .collect::<Result<Vec<_>, _>>()?;
        self.preflight_mutation_parents(&resolved)?;

        for mutation in resolved {
            match mutation {
                ResolvedMutation::Write {
                    path,
                    content,
                    remove_source,
                } => {
                    if let Some(parent) = path.parent() {
                        fs::create_dir_all(parent)
                            .map_err(|error| backend_error("create_directory", parent, error))?;
                    }
                    fs::write(&path, content)
                        .map_err(|error| backend_error("patch_write", &path, error))?;
                    if let Some(source) = remove_source {
                        fs::remove_file(&source)
                            .map_err(|error| backend_error("patch_move_remove", &source, error))?;
                    }
                }
                ResolvedMutation::Delete { path } => {
                    fs::remove_file(&path)
                        .map_err(|error| backend_error("patch_delete", &path, error))?;
                }
            }
        }
        Ok(())
    }

    fn preflight_mutation_parents(
        &self,
        mutations: &[ResolvedMutation],
    ) -> Result<(), FileToolError> {
        let mut virtual_entries = BTreeMap::<String, VirtualEntryState>::new();
        for mutation in mutations {
            match mutation {
                ResolvedMutation::Write {
                    path,
                    remove_source,
                    ..
                } => {
                    self.validate_write_parent(path, &virtual_entries)?;
                    virtual_entries.insert(self.host_path_key(path), VirtualEntryState::File);
                    if let Some(source) = remove_source {
                        virtual_entries
                            .insert(self.host_path_key(source), VirtualEntryState::Removed);
                    }
                }
                ResolvedMutation::Delete { path } => {
                    virtual_entries.insert(self.host_path_key(path), VirtualEntryState::Removed);
                }
            }
        }
        Ok(())
    }

    fn validate_write_parent(
        &self,
        path: &Path,
        virtual_entries: &BTreeMap<String, VirtualEntryState>,
    ) -> Result<(), FileToolError> {
        for parent in path.parent().into_iter().flat_map(Path::ancestors) {
            match virtual_entries.get(&self.host_path_key(parent)) {
                Some(VirtualEntryState::File) => {
                    return Err(non_directory_parent(path, parent));
                }
                Some(VirtualEntryState::Removed) => continue,
                None => {}
            }
            match fs::metadata(parent) {
                Ok(metadata) if metadata.is_dir() => {}
                Ok(_) => return Err(non_directory_parent(path, parent)),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    match fs::symlink_metadata(parent) {
                        Ok(_) => return Err(non_directory_parent(path, parent)),
                        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                        Err(error) => {
                            return Err(backend_error("patch_preflight", parent, error));
                        }
                    }
                }
                Err(error) => return Err(backend_error("patch_preflight", parent, error)),
            }
        }
        Ok(())
    }

    fn host_path_key(&self, path: &Path) -> String {
        let key = path.to_string_lossy().into_owned();
        if self.case_sensitive_paths {
            key
        } else {
            key.to_lowercase()
        }
    }

    pub(super) fn read_directory_page(
        &self,
        path: &str,
        maximum_entries: usize,
    ) -> Result<DirectoryListing, FileToolError> {
        if maximum_entries == 0 || maximum_entries > MAX_WALK_ENTRIES {
            return Err(FileToolError::InvalidPattern {
                mode: "directory_limit",
                message: format!("limit must be between 1 and {MAX_WALK_ENTRIES}"),
            });
        }
        let resolved = self.resolve_read(path)?;
        let entries = fs::read_dir(&resolved)
            .map_err(|error| backend_error("read_directory", &resolved, error))?;
        let mut visible: BTreeMap<String, DirectoryEntry> = BTreeMap::new();
        let mut truncated = false;
        for entry in entries {
            let entry = entry.map_err(|error| backend_error("read_directory", &resolved, error))?;
            let child = entry.path();
            if !matches!(
                self.policy.check_file_read(&child),
                PermissionDecision::Allow
            ) {
                continue;
            }
            let Some(path) = child.to_str() else {
                continue;
            };
            if visible.len() == maximum_entries
                && visible
                    .last_key_value()
                    .is_some_and(|(last, _)| path >= last.as_str())
            {
                truncated = true;
                continue;
            }
            visible.insert(
                path.to_string(),
                DirectoryEntry::new(
                    BackendPath::new(BackendKind::HostFs, path)?,
                    metadata_for_path(&child)?,
                ),
            );
            if visible.len() > maximum_entries {
                visible.pop_last();
                truncated = true;
            }
        }
        Ok(DirectoryListing {
            entries: visible.into_values().collect(),
            truncated,
        })
    }

    fn resolve_mutation(
        &self,
        mutation: &PlannedMutation,
    ) -> Result<ResolvedMutation, FileToolError> {
        match mutation {
            PlannedMutation::Write {
                path,
                content,
                remove_source,
            } => {
                validate_write_size(content)?;
                Ok(ResolvedMutation::Write {
                    path: self.resolve_write(path.as_str())?,
                    content: content.clone(),
                    remove_source: remove_source
                        .as_ref()
                        .map(|source| self.resolve_entry_write(source.as_str()))
                        .transpose()?,
                })
            }
            PlannedMutation::Delete { path } => Ok(ResolvedMutation::Delete {
                path: self.resolve_entry_write(path.as_str())?,
            }),
        }
    }
}

fn validate_write_size(content: &str) -> Result<(), FileToolError> {
    if content.len() > MAX_RESPONSE_BYTES {
        return Err(FileToolError::ResponseLimitExceeded {
            requested: content.len(),
            maximum: MAX_RESPONSE_BYTES,
        });
    }
    Ok(())
}

enum ResolvedMutation {
    Write {
        path: PathBuf,
        content: String,
        remove_source: Option<PathBuf>,
    },
    Delete {
        path: PathBuf,
    },
}

#[derive(Clone, Copy)]
enum VirtualEntryState {
    File,
    Removed,
}

impl FileBackend for HostFsBackend {
    fn capabilities(&self) -> BackendCapabilities {
        BackendCapabilities::new(
            BackendKind::HostFs,
            PathSemantics::HostNative,
            MutationAtomicity::BestEffort,
        )
    }
}

impl ReadBackend for HostFsBackend {
    fn open_read(&self, path: &str) -> Result<Box<dyn ReadSeek + Send>, FileToolError> {
        let resolved = self.resolve_read(path)?;
        fs::File::open(&resolved)
            .map(|file| Box::new(file) as Box<dyn ReadSeek + Send>)
            .map_err(|error| backend_error("open_read", &resolved, error))
    }
}

impl WalkBackend for HostFsBackend {
    fn metadata(&self, path: &str) -> Result<EntryMetadata, FileToolError> {
        let resolved = self.resolve_read_entry(path)?;
        metadata_for_path(&resolved)
    }

    fn read_directory(&self, path: &str) -> Result<DirectoryListing, FileToolError> {
        self.read_directory_page(path, MAX_WALK_ENTRIES)
    }
}

impl PatchSnapshot for HostFsBackend {
    fn file_entry_exists(&self, path: &str) -> Result<bool, PatchError> {
        let resolved = self
            .resolve_entry_write(path)
            .map_err(|error| PatchError::Snapshot {
                path: path.to_string(),
                message: error.to_string(),
            })?;
        match fs::symlink_metadata(&resolved) {
            Ok(metadata) => Ok(metadata.file_type().is_symlink() || metadata.is_file()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(false),
            Err(error) => Err(PatchError::Snapshot {
                path: path.to_string(),
                message: error.to_string(),
            }),
        }
    }

    fn canonical_key(&self, path: &str) -> Result<String, PatchError> {
        let resolved = self
            .resolve_read(path)
            .map_err(|error| PatchError::Snapshot {
                path: path.to_string(),
                message: error.to_string(),
            })?;
        let key = resolved.to_string_lossy().into_owned();
        if self.case_sensitive_paths {
            Ok(key)
        } else {
            Ok(key.to_lowercase())
        }
    }

    fn canonical_entry_key(&self, path: &str) -> Result<String, PatchError> {
        let resolved = self
            .resolve_entry_write(path)
            .map_err(|error| PatchError::Snapshot {
                path: path.to_string(),
                message: error.to_string(),
            })?;
        let key = resolved.to_string_lossy().into_owned();
        if self.case_sensitive_paths {
            Ok(key)
        } else {
            Ok(key.to_lowercase())
        }
    }

    fn read_text(&self, path: &str) -> Result<Option<String>, PatchError> {
        const MAX_PATCH_FILE_BYTES: u64 = MAX_RESPONSE_BYTES as u64;

        let resolved = self
            .resolve_read(path)
            .map_err(|error| PatchError::Snapshot {
                path: path.to_string(),
                message: error.to_string(),
            })?;
        let file = match fs::File::open(&resolved) {
            Ok(file) => file,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => {
                return Err(PatchError::Snapshot {
                    path: path.to_string(),
                    message: error.to_string(),
                });
            }
        };
        let mut bytes = Vec::new();
        file.take(MAX_PATCH_FILE_BYTES + 1)
            .read_to_end(&mut bytes)
            .map_err(|error| PatchError::Snapshot {
                path: path.to_string(),
                message: error.to_string(),
            })?;
        if bytes.len() as u64 > MAX_PATCH_FILE_BYTES {
            return Err(PatchError::Snapshot {
                path: path.to_string(),
                message: format!("file exceeds the {MAX_PATCH_FILE_BYTES}-byte patch limit"),
            });
        }
        if bytes.contains(&0) {
            return Err(PatchError::Snapshot {
                path: path.to_string(),
                message: "binary content cannot be patched".to_string(),
            });
        }
        String::from_utf8(bytes)
            .map(Some)
            .map_err(|error| PatchError::Snapshot {
                path: path.to_string(),
                message: error.to_string(),
            })
    }
}

#[cfg(windows)]
fn host_paths_are_case_sensitive(_root: &Path) -> bool {
    false
}

#[cfg(unix)]
fn host_paths_are_case_sensitive(root: &Path) -> bool {
    use std::os::unix::fs::MetadataExt;

    let mut current = fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
    loop {
        let Some(parent) = current.parent() else {
            break;
        };
        if let Some(alternate_name) = current
            .file_name()
            .and_then(|name| name.to_str())
            .and_then(toggle_ascii_case)
        {
            let alternate = parent.join(alternate_name);
            if let (Ok(actual), Ok(probe)) = (fs::metadata(&current), fs::metadata(alternate)) {
                if actual.dev() == probe.dev() && actual.ino() == probe.ino() {
                    return false;
                }
            }
        }
        current = parent.to_path_buf();
    }
    true
}

#[cfg(not(any(unix, windows)))]
fn host_paths_are_case_sensitive(_root: &Path) -> bool {
    true
}

#[cfg(any(unix, test))]
fn toggle_ascii_case(value: &str) -> Option<String> {
    let mut bytes = value.as_bytes().to_vec();
    let byte = bytes.iter_mut().find(|byte| byte.is_ascii_alphabetic())?;
    *byte = if byte.is_ascii_lowercase() {
        byte.to_ascii_uppercase()
    } else {
        byte.to_ascii_lowercase()
    };
    String::from_utf8(bytes).ok()
}

fn reject_cross_backend_path(path: &str) -> Result<(), FileToolError> {
    if path
        .get(..7)
        .is_some_and(|prefix| prefix.eq_ignore_ascii_case("corefs:"))
    {
        return Err(FileToolError::InvalidPath {
            path: path.to_string(),
            reason: "CoreFS paths are not accepted by HostFS tools".to_string(),
        });
    }
    Ok(())
}

fn metadata_for_path(path: &Path) -> Result<EntryMetadata, FileToolError> {
    let link_metadata =
        fs::symlink_metadata(path).map_err(|error| backend_error("metadata", path, error))?;
    let is_symlink = link_metadata.file_type().is_symlink();
    let metadata = if is_symlink {
        fs::metadata(path).map_err(|error| backend_error("metadata", path, error))?
    } else {
        link_metadata
    };
    let kind = if metadata.is_dir() {
        EntryKind::Directory
    } else if metadata.is_file() {
        EntryKind::File
    } else {
        EntryKind::Other
    };
    Ok(EntryMetadata {
        kind,
        is_symlink,
        size: metadata.len(),
        content: anima_file_tools::ContentClassification::Unknown,
    })
}

fn backend_error(operation: &'static str, path: &Path, error: std::io::Error) -> FileToolError {
    FileToolError::Backend {
        operation,
        path: path.to_string_lossy().into_owned(),
        message: error.to_string(),
    }
}

fn non_directory_parent(path: &Path, parent: &Path) -> FileToolError {
    FileToolError::InvalidPath {
        path: path.to_string_lossy().into_owned(),
        reason: format!("write parent {} is not a directory", parent.display()),
    }
}

#[cfg(test)]
mod tests {
    use anima_file_tools::{
        parse_patch, plan_patch, walk_page, BackendKind, BackendPath, FileBackend, FileToolError,
        MutationAtomicity, OperationControl, OperationLimits, PatchSnapshot, ReadBackend,
        WalkOptions,
    };

    use super::{toggle_ascii_case, HostFsBackend};
    use crate::permissions::PermissionPolicy;

    #[test]
    fn host_backend_reads_authorized_workspace_files() {
        let root = test_workspace();
        std::fs::write(root.join("note.txt"), "hello").unwrap();
        let backend = HostFsBackend::new(PermissionPolicy::read_only(root.clone()));

        let mut reader = backend
            .open_read(root.join("note.txt").to_str().unwrap())
            .unwrap();
        let mut contents = String::new();
        std::io::Read::read_to_string(&mut reader, &mut contents).unwrap();

        assert_eq!(contents, "hello");
        assert_eq!(backend.capabilities().backend(), BackendKind::HostFs);
    }

    #[test]
    fn host_backend_rejects_corefs_uris_without_path_inference() {
        let root = test_workspace();
        let backend = HostFsBackend::new(PermissionPolicy::read_only(root));

        for path in [
            "corefs://object/123",
            "corefs:/notes/today.md",
            "corefs:notes",
        ] {
            let Err(error) = backend.open_read(path) else {
                panic!("HostFS must reject CoreFS path form: {path}");
            };
            assert!(matches!(error, FileToolError::InvalidPath { .. }));
        }
    }

    #[test]
    fn case_insensitive_host_keys_fold_on_every_operating_system() {
        let root = test_workspace();
        let backend =
            HostFsBackend::with_case_sensitivity(PermissionPolicy::read_only(root.clone()), false);

        let upper = backend
            .canonical_key(root.join("Case.txt").to_str().unwrap())
            .unwrap();
        let lower = backend
            .canonical_key(root.join("case.txt").to_str().unwrap())
            .unwrap();

        assert_eq!(upper, lower);
    }

    #[test]
    fn case_probe_changes_one_ascii_letter_without_touching_the_rest() {
        assert_eq!(toggle_ascii_case("Project-123"), Some("project-123".into()));
        assert_eq!(toggle_ascii_case("123"), None);
    }

    #[test]
    fn shared_walk_over_host_backend_never_traverses_directory_symlinks() {
        let root = test_workspace();
        let target = root.join("target");
        let link = root.join("link");
        std::fs::create_dir_all(&target).unwrap();
        std::fs::write(target.join("secret.txt"), "hidden through link").unwrap();
        if create_directory_symlink(&target, &link).is_err() {
            return;
        }
        let backend = HostFsBackend::new(PermissionPolicy::read_only(root.clone()));

        let page = walk_page(
            &backend,
            BackendPath::new(BackendKind::HostFs, root.to_string_lossy()).unwrap(),
            WalkOptions {
                page_size: 100,
                cursor: None,
                include_directories: true,
            },
            OperationLimits::default().validate().unwrap(),
            OperationControl::default(),
        )
        .unwrap();

        let link_entry = page
            .entries
            .iter()
            .find(|entry| entry.path.as_str().ends_with("link"))
            .unwrap();
        assert!(link_entry.is_symlink);
        assert!(!page
            .entries
            .iter()
            .any(|entry| entry.path.as_str().contains("link\\secret.txt")));
    }

    #[test]
    fn shared_walk_never_traverses_a_directory_symlink_used_as_the_root() {
        let root = test_workspace();
        let target = root.join("target");
        let link = root.join("link");
        std::fs::create_dir_all(&target).unwrap();
        std::fs::write(target.join("secret.txt"), "hidden through root link").unwrap();
        if create_directory_symlink(&target, &link).is_err() {
            return;
        }
        let backend = HostFsBackend::new(PermissionPolicy::read_only(root));

        let page = walk_page(
            &backend,
            BackendPath::new(BackendKind::HostFs, link.to_string_lossy()).unwrap(),
            WalkOptions {
                page_size: 100,
                cursor: None,
                include_directories: true,
            },
            OperationLimits::default().validate().unwrap(),
            OperationControl::default(),
        )
        .unwrap();

        assert!(page.entries.is_empty());
    }

    #[test]
    fn directory_listing_skips_dangling_symlinks_without_aborting() {
        let root = test_workspace();
        let target = root
            .parent()
            .unwrap()
            .join(format!("missing-{}.txt", uuid::Uuid::new_v4()));
        let link = root.join("dangling.txt");
        std::fs::write(root.join("note.txt"), "visible").unwrap();
        if create_dangling_file_symlink(&target, &link).is_err() {
            return;
        }
        let backend = HostFsBackend::new(PermissionPolicy::read_only(root.clone()));

        let listing = backend
            .read_directory_page(root.to_str().unwrap(), 100)
            .unwrap();

        assert!(listing
            .entries
            .iter()
            .any(|entry| entry.path.as_str().ends_with("note.txt")));
        assert!(!listing
            .entries
            .iter()
            .any(|entry| entry.path.as_str().ends_with("dangling.txt")));
    }

    #[cfg(unix)]
    #[test]
    fn directory_listing_skips_non_utf8_entries_without_aborting() {
        use std::ffi::OsString;
        use std::os::unix::ffi::OsStringExt;

        let root = test_workspace();
        std::fs::write(root.join("note.txt"), "visible").unwrap();
        std::fs::write(
            root.join(OsString::from_vec(b"invalid-\xff.txt".to_vec())),
            "skip me",
        )
        .unwrap();
        let backend = HostFsBackend::new(PermissionPolicy::read_only(root.clone()));

        let listing = backend
            .read_directory_page(root.to_str().unwrap(), 100)
            .unwrap();

        assert_eq!(listing.entries.len(), 1);
        assert!(listing.entries[0].path.as_str().ends_with("note.txt"));
    }

    #[test]
    fn patch_delete_removes_the_named_symlink_instead_of_its_target() {
        let root = test_workspace();
        let target = root.join("target.txt");
        let link = root.join("link.txt");
        std::fs::write(&target, "keep me").unwrap();
        if create_file_symlink(&target, &link).is_err() {
            return;
        }
        let backend = HostFsBackend::new(PermissionPolicy::workspace_write(root));
        let patch =
            parse_patch("*** Begin Patch\n*** Delete File: link.txt\n*** End Patch").unwrap();
        let plan = plan_patch(&backend, &patch, MutationAtomicity::BestEffort).unwrap();

        backend.apply_plan(&plan).unwrap();

        assert!(!link.exists());
        assert_eq!(std::fs::read_to_string(target).unwrap(), "keep me");
    }

    #[test]
    fn patch_delete_removes_a_dangling_symlink_without_reading_its_target() {
        let root = test_workspace();
        let target = root
            .parent()
            .unwrap()
            .join(format!("missing-{}.txt", uuid::Uuid::new_v4()));
        let link = root.join("link.txt");
        if create_dangling_file_symlink(&target, &link).is_err() {
            return;
        }
        assert!(std::fs::symlink_metadata(&link).is_ok());
        let backend = HostFsBackend::new(PermissionPolicy::workspace_write(root));
        let patch =
            parse_patch("*** Begin Patch\n*** Delete File: link.txt\n*** End Patch").unwrap();

        let plan = plan_patch(&backend, &patch, MutationAtomicity::BestEffort).unwrap();
        backend.apply_plan(&plan).unwrap();

        assert!(std::fs::symlink_metadata(link).is_err());
        assert!(!target.exists());
    }

    fn test_workspace() -> std::path::PathBuf {
        let root = std::env::temp_dir().join(format!("animus-hostfs-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&root).unwrap();
        root
    }

    #[cfg(unix)]
    fn create_directory_symlink(
        target: &std::path::Path,
        link: &std::path::Path,
    ) -> std::io::Result<()> {
        std::os::unix::fs::symlink(target, link)
    }

    #[cfg(windows)]
    fn create_directory_symlink(
        target: &std::path::Path,
        link: &std::path::Path,
    ) -> std::io::Result<()> {
        if std::os::windows::fs::symlink_dir(target, link).is_ok() {
            return Ok(());
        }
        if create_directory_junction(target, link).is_ok() {
            return Ok(());
        }
        create_wsl_symlink(target, link)
    }

    #[cfg(windows)]
    fn create_directory_junction(
        target: &std::path::Path,
        link: &std::path::Path,
    ) -> std::io::Result<()> {
        let status = std::process::Command::new("cmd.exe")
            .args(["/C", "mklink", "/J"])
            .arg(link)
            .arg(target)
            .status()?;
        if status.success() {
            Ok(())
        } else {
            Err(std::io::Error::other("mklink /J failed"))
        }
    }

    #[cfg(unix)]
    fn create_file_symlink(
        target: &std::path::Path,
        link: &std::path::Path,
    ) -> std::io::Result<()> {
        std::os::unix::fs::symlink(target, link)
    }

    #[cfg(windows)]
    fn create_file_symlink(
        target: &std::path::Path,
        link: &std::path::Path,
    ) -> std::io::Result<()> {
        std::os::windows::fs::symlink_file(target, link)
    }

    #[cfg(unix)]
    fn create_dangling_file_symlink(
        target: &std::path::Path,
        link: &std::path::Path,
    ) -> std::io::Result<()> {
        create_file_symlink(target, link)
    }

    #[cfg(windows)]
    fn create_dangling_file_symlink(
        target: &std::path::Path,
        link: &std::path::Path,
    ) -> std::io::Result<()> {
        if create_file_symlink(target, link).is_ok() {
            return Ok(());
        }
        create_wsl_symlink(target, link)
    }

    #[cfg(windows)]
    fn create_wsl_symlink(target: &std::path::Path, link: &std::path::Path) -> std::io::Result<()> {
        use std::process::Command;

        fn wsl_path(path: &std::path::Path) -> std::io::Result<String> {
            let output = Command::new("wsl.exe")
                .arg("wslpath")
                .arg("-a")
                .arg(path.to_string_lossy().replace('\\', "/"))
                .output()?;
            if !output.status.success() {
                return Err(std::io::Error::other("wslpath failed"));
            }
            Ok(String::from_utf8_lossy(&output.stdout).trim().to_string())
        }

        let target = wsl_path(target)?;
        let link = wsl_path(link)?;
        let output = Command::new("wsl.exe")
            .arg("-e")
            .arg("ln")
            .arg("-s")
            .arg(target)
            .arg(link)
            .output()?;
        if output.status.success() {
            Ok(())
        } else {
            Err(std::io::Error::other("wsl ln failed"))
        }
    }
}
