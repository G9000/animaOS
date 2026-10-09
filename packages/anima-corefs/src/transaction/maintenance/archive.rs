//! Capability-bounded opaque preparation archive moves and resumable sweeps.
use super::*;

const DIRECTORIES: [&str; 4] = ["snapshots", "descriptors", "intent", "receipts"];

#[derive(Clone, Debug, Deserialize, Serialize, Eq, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct ArchiveFile {
    hash: String,
    bytes: u64,
}

fn checked_directory(parent: &Dir, name: &str) -> Result<Dir, MaintenanceError> {
    let dir = parent.open_dir(name)?;
    validate_linked_directory(parent, name, &dir)?;
    Ok(dir)
}

pub(super) fn source(
    coordinator: &CoreCommitCoordinator,
    record: &Candidate,
) -> Result<Dir, MaintenanceError> {
    if let Some(id) = &record.preparation_id {
        OpaqueId::parse(id).map_err(|_| MaintenanceError::Evidence)?;
        if record.name != *id {
            return Err(MaintenanceError::Evidence);
        }
        checked_directory(&coordinator.fs_dir, "preparations")
    } else if record.catalog {
        Ok(coordinator.catalogs_dir.try_clone()?)
    } else {
        Ok(coordinator.objects_dir.try_clone()?)
    }
}

pub(super) fn read(parent: &Dir, id: &str, partial: bool) -> Result<Candidate, MaintenanceError> {
    OpaqueId::parse(id).map_err(|_| MaintenanceError::Evidence)?;
    let archive = checked_directory(parent, id)?;
    for entry in archive.entries()? {
        let name = entry?
            .file_name()
            .into_string()
            .map_err(|_| MaintenanceError::Evidence)?;
        if !DIRECTORIES.contains(&name.as_str()) {
            return Err(MaintenanceError::Evidence);
        }
    }
    let mut files = BTreeMap::new();
    let mut total = 0_u64;
    for directory in DIRECTORIES {
        let child = match checked_directory(&archive, directory) {
            Ok(dir) => dir,
            Err(MaintenanceError::Io(e)) if partial && e.kind() == io::ErrorKind::NotFound => {
                continue
            }
            Err(e) => return Err(e),
        };
        for entry in child.entries()? {
            if files.len() >= 200_000 {
                return Err(MaintenanceError::Evidence);
            }
            let name = entry?
                .file_name()
                .into_string()
                .map_err(|_| MaintenanceError::Evidence)?;
            if !name.ends_with(".acore") || name.contains('/') || name.contains('\\') {
                return Err(MaintenanceError::Evidence);
            }
            let record = file_record(&child, &name, false)?;
            total = total
                .checked_add(record.bytes)
                .ok_or(MaintenanceError::Evidence)?;
            files.insert(
                format!("{directory}/{name}"),
                ArchiveFile {
                    hash: record.hash,
                    bytes: record.bytes,
                },
            );
        }
    }
    let digest = hash(&serde_json::to_vec(&files).map_err(|_| MaintenanceError::Evidence)?);
    Ok(Candidate {
        catalog: false,
        name: id.to_owned(),
        hash: digest,
        bytes: total,
        preparation_id: Some(id.to_owned()),
        archive_files: files,
    })
}

pub(super) fn record(
    parent: &Dir,
    expected: &Candidate,
    partial: bool,
) -> Result<Candidate, MaintenanceError> {
    if expected.preparation_id.is_some() {
        let actual = read(parent, &expected.name, partial)?;
        if partial {
            if actual
                .archive_files
                .iter()
                .any(|(name, file)| expected.archive_files.get(name) != Some(file))
            {
                return Err(MaintenanceError::Evidence);
            }
            // Missing files are progress only after authenticated sweep authorization.
            Ok(expected.clone())
        } else {
            Ok(actual)
        }
    } else {
        file_record(parent, &expected.name, expected.catalog)
    }
}

#[cfg(windows)]
fn directory_handle(parent: &Dir, name: &str) -> Result<cap_std::fs::File, MaintenanceError> {
    use cap_std::fs::OpenOptionsExt;
    use windows_sys::Win32::Storage::FileSystem::{
        DELETE, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT, FILE_FLAG_WRITE_THROUGH,
        FILE_SHARE_READ,
    };
    let mut options = cap_std::fs::OpenOptions::new();
    options
        .read(true)
        .access_mode(windows_sys::Win32::Foundation::GENERIC_READ | DELETE)
        .share_mode(FILE_SHARE_READ)
        .custom_flags(
            FILE_FLAG_WRITE_THROUGH | FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT,
        );
    let file = parent.open_with(name, &options)?;
    let metadata = Metadata::from_file(&file.try_clone()?.into_std())?;
    let linked = parent.symlink_metadata(name)?;
    if !metadata.is_dir()
        || !linked.is_dir()
        || linked.is_symlink()
        || !same_file_identity(&metadata, &linked)
    {
        return Err(MaintenanceError::Evidence);
    }
    Ok(file)
}

pub(super) fn move_candidate(
    source: &Dir,
    target: &Dir,
    record: &Candidate,
) -> Result<(), MaintenanceError> {
    if record.preparation_id.is_none() {
        return durable_move(source, &record.name, target);
    }
    if target.symlink_metadata(&record.name).is_ok() {
        return Err(MaintenanceError::Evidence);
    }
    #[cfg(windows)]
    {
        let file = directory_handle(source, &record.name)?;
        crate::publication::rename_file_by_handle(&file, target, OsStr::new(&record.name), false)?;
    }
    #[cfg(not(windows))]
    {
        source.rename(&record.name, target, &record.name)?;
    }
    sync_directory(source)?;
    sync_directory(target)?;
    Ok(())
}

fn remove_empty_directory(parent: &Dir, name: &str) -> Result<(), MaintenanceError> {
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::Storage::FileSystem::{
            FileDispositionInfo, SetFileInformationByHandle, FILE_DISPOSITION_INFO,
        };
        let file = directory_handle(parent, name)?;
        let disposition = FILE_DISPOSITION_INFO { DeleteFile: 1 };
        let result = unsafe {
            SetFileInformationByHandle(
                file.as_raw_handle(),
                FileDispositionInfo,
                (&disposition as *const FILE_DISPOSITION_INFO).cast(),
                std::mem::size_of::<FILE_DISPOSITION_INFO>() as u32,
            )
        };
        if result == 0 {
            return Err(io::Error::last_os_error().into());
        }
        drop(file);
    }
    #[cfg(not(windows))]
    {
        parent.remove_dir(name)?;
    }
    sync_directory(parent)?;
    Ok(())
}

pub(super) fn sweep(
    parent: &Dir,
    expected: &Candidate,
    budget: &mut usize,
) -> Result<bool, MaintenanceError> {
    if expected.preparation_id.is_none() {
        if *budget == 0 {
            return Ok(false);
        }
        durable_remove(parent, &expected.name)?;
        *budget -= 1;
        return Ok(true);
    }
    record(parent, expected, true)?;
    let initial_budget = *budget;
    let archive = checked_directory(parent, &expected.name)?;
    for directory in DIRECTORIES {
        let child = match checked_directory(&archive, directory) {
            Ok(dir) => dir,
            Err(MaintenanceError::Io(e)) if e.kind() == io::ErrorKind::NotFound => continue,
            Err(e) => return Err(e),
        };
        let names = child
            .entries()?
            .map(|entry| entry.map(|entry| entry.file_name()))
            .collect::<Result<Vec<_>, _>>()?;
        for name in names {
            if *budget == 0 {
                return Ok(false);
            }
            let name = name.into_string().map_err(|_| MaintenanceError::Evidence)?;
            let wanted = expected
                .archive_files
                .get(&format!("{directory}/{name}"))
                .ok_or(MaintenanceError::Evidence)?;
            let actual = file_record(&child, &name, false)?;
            if actual.hash != wanted.hash || actual.bytes != wanted.bytes {
                return Err(MaintenanceError::Evidence);
            }
            durable_remove(&child, &name)?;
            *budget -= 1;
        }
        drop(child);
        remove_empty_directory(&archive, directory)?;
    }
    if *budget == initial_budget {
        if *budget == 0 {
            return Ok(false);
        }
        *budget -= 1;
    }
    drop(archive);
    remove_empty_directory(parent, &expected.name)?;
    Ok(true)
}
