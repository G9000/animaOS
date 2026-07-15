use std::ffi::{OsStr, OsString};
use std::io::{self, Write};
use std::path::{Component, Path};

use cap_std::ambient_authority;
use cap_std::fs::{Dir, File, OpenOptions};
use getrandom::getrandom;

#[cfg(unix)]
use cap_std::fs::OpenOptionsExt as _;
#[cfg(windows)]
use cap_std::fs::OpenOptionsExt as _;

#[cfg(any(unix, test))]
const TEMPORARY_FILE_MODE: u32 = 0o600;

#[cfg(windows)]
const WINDOWS_TEMPORARY_CUSTOM_FLAGS: u32 =
    windows_sys::Win32::Storage::FileSystem::FILE_FLAG_WRITE_THROUGH;
#[cfg(windows)]
type RenameInfoStorageWord = usize;

/// Atomically publish `payload` and return only after its directory entry is durable.
pub fn atomic_publish(target: &Path, payload: &[u8]) -> io::Result<()> {
    let parent = target.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "publication target has no parent",
        )
    })?;
    let name = target.file_name().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "publication target has no file name",
        )
    })?;
    let dir = Dir::open_ambient_dir(parent, ambient_authority())?;
    atomic_publish_in(&dir, name, payload)
}

/// Durably publish a new immutable file without replacing an existing revision.
pub fn publish_immutable(target: &Path, payload: &[u8]) -> io::Result<()> {
    let parent = target.parent().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "publication target has no parent",
        )
    })?;
    let name = target.file_name().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "publication target has no file name",
        )
    })?;
    let dir = Dir::open_ambient_dir(parent, ambient_authority())?;
    publish_immutable_in(&dir, name, payload)
}

pub(crate) fn atomic_publish_in(dir: &Dir, target: &OsStr, payload: &[u8]) -> io::Result<()> {
    validate_file_name(target)?;
    let (mut temporary, temporary_name) = create_temporary_in(dir, target)?;
    let result = (|| {
        temporary.write_all(payload)?;
        temporary.sync_all()?;
        replace_staged_in(dir, &temporary, &temporary_name, target)
    })();
    drop(temporary);
    if result.is_err() {
        let _ = dir.remove_file(&temporary_name);
    }
    result
}

pub(crate) fn publish_immutable_in(dir: &Dir, target: &OsStr, payload: &[u8]) -> io::Result<()> {
    let (mut temporary, temporary_name) = create_temporary_in(dir, target)?;
    let result = (|| {
        temporary.write_all(payload)?;
        temporary.sync_all()?;
        publish_staged_immutable_in(dir, &temporary, &temporary_name, target)
    })();
    drop(temporary);
    if result.is_err() {
        let _ = dir.remove_file(&temporary_name);
    }
    result
}

pub(crate) fn create_temporary_in(dir: &Dir, target: &OsStr) -> io::Result<(File, OsString)> {
    validate_file_name(target)?;
    for _ in 0..16 {
        let mut random = [0_u8; 8];
        getrandom(&mut random).map_err(io::Error::other)?;
        let suffix = u64::from_ne_bytes(random);
        let temporary_name =
            OsString::from(format!(".{}.{}.tmp", target.to_string_lossy(), suffix));
        let mut options = OpenOptions::new();
        options.read(true).write(true).create_new(true);
        #[cfg(unix)]
        options.mode(TEMPORARY_FILE_MODE);
        #[cfg(windows)]
        options
            .access_mode(
                windows_sys::Win32::Foundation::GENERIC_WRITE
                    | windows_sys::Win32::Foundation::GENERIC_READ
                    | windows_sys::Win32::Storage::FileSystem::DELETE,
            )
            .share_mode(
                windows_sys::Win32::Storage::FileSystem::FILE_SHARE_READ
                    | windows_sys::Win32::Storage::FileSystem::FILE_SHARE_WRITE,
            )
            // Keep the exact staged file pinned against path substitution while
            // making SetFileInformationByHandle rename metadata write-through.
            .custom_flags(WINDOWS_TEMPORARY_CUSTOM_FLAGS);
        match dir.open_with(&temporary_name, &options) {
            Ok(file) => return Ok((file, temporary_name)),
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        "could not allocate a unique publication file",
    ))
}

#[cfg(any(unix, test))]
pub(crate) fn is_temporary_name_for_target(candidate: &OsStr, target: &OsStr) -> bool {
    let Some(candidate) = candidate.to_str() else {
        return false;
    };
    let prefix = format!(".{}.", target.to_string_lossy());
    let Some(suffix) = candidate
        .strip_prefix(&prefix)
        .and_then(|value| value.strip_suffix(".tmp"))
    else {
        return false;
    };
    suffix
        .parse::<u64>()
        .is_ok_and(|value| value.to_string() == suffix)
}

pub(crate) fn publish_staged_immutable_in(
    dir: &Dir,
    temporary: &File,
    temporary_name: &OsStr,
    target: &OsStr,
) -> io::Result<()> {
    validate_file_name(temporary_name)?;
    validate_file_name(target)?;

    publish_staged_immutable_platform(dir, temporary, temporary_name, target)
}

#[cfg(not(windows))]
pub(crate) fn sync_directory(dir: &Dir) -> io::Result<()> {
    dir.try_clone()?.into_std_file().sync_all()
}

pub(crate) fn durable_create_directory_in(dir: &Dir, target: &OsStr) -> io::Result<()> {
    validate_file_name(target)?;
    durable_create_directory_platform(dir, target)
}

fn validate_file_name(value: &OsStr) -> io::Result<()> {
    let mut components = Path::new(value).components();
    if matches!(components.next(), Some(Component::Normal(_))) && components.next().is_none() {
        Ok(())
    } else {
        Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "publication name must be one relative file component",
        ))
    }
}

#[cfg(not(windows))]
fn replace_staged_in(
    dir: &Dir,
    _temporary: &File,
    temporary_name: &OsStr,
    target: &OsStr,
) -> io::Result<()> {
    dir.rename(temporary_name, dir, target)?;
    sync_directory(dir)
}

#[cfg(windows)]
fn replace_staged_in(
    dir: &Dir,
    temporary: &File,
    _temporary_name: &OsStr,
    target: &OsStr,
) -> io::Result<()> {
    rename_file_by_handle(temporary, dir, target, true)?;
    temporary.sync_all()
}

#[cfg(not(windows))]
fn publish_staged_immutable_platform(
    dir: &Dir,
    _temporary: &File,
    temporary_name: &OsStr,
    target: &OsStr,
) -> io::Result<()> {
    // Creating a hard link is an atomic no-replace publication primitive on
    // the same filesystem. It cannot overwrite a substituted destination, and
    // cleanup removes only the private staging entry.
    dir.hard_link(temporary_name, dir, target)?;
    sync_directory(dir)?;
    dir.remove_file(temporary_name)?;
    sync_directory(dir)
}

#[cfg(windows)]
fn publish_staged_immutable_platform(
    dir: &Dir,
    temporary: &File,
    _temporary_name: &OsStr,
    target: &OsStr,
) -> io::Result<()> {
    rename_file_by_handle(temporary, dir, target, false)?;
    temporary.sync_all()
}

#[cfg(not(windows))]
fn durable_create_directory_platform(dir: &Dir, target: &OsStr) -> io::Result<()> {
    dir.create_dir(target)?;
    sync_directory(dir)
}

#[cfg(windows)]
fn durable_create_directory_platform(dir: &Dir, target: &OsStr) -> io::Result<()> {
    for _ in 0..16 {
        let mut random = [0_u8; 16];
        getrandom(&mut random).map_err(io::Error::other)?;
        let temporary = OsString::from(format!(".dir-{}.tmp", hex_bytes(&random)));
        match dir.create_dir(&temporary) {
            Ok(()) => {
                let result = move_path_write_through(dir, &temporary, target, false);
                if result.is_err() {
                    let _ = dir.remove_dir(&temporary);
                }
                return result;
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        "could not allocate a unique directory staging name",
    ))
}

#[cfg(windows)]
fn rename_file_by_handle(
    source: &File,
    target_dir: &Dir,
    target: &OsStr,
    replace: bool,
) -> io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        FileRenameInfo, SetFileInformationByHandle, FILE_RENAME_INFO, FILE_RENAME_INFO_0,
    };

    let name: Vec<u16> = directory_path(target_dir)?
        .join(target)
        .as_os_str()
        .encode_wide()
        .collect();
    let header_size = std::mem::size_of::<FILE_RENAME_INFO>() - std::mem::size_of::<u16>();
    let total_size = header_size
        .checked_add(name.len() * std::mem::size_of::<u16>())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "rename target too long"))?;
    let storage_word_size = std::mem::size_of::<RenameInfoStorageWord>();
    let storage_len = total_size
        .checked_add(storage_word_size - 1)
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "rename buffer too large"))?
        / storage_word_size;
    let mut buffer = vec![0 as RenameInfoStorageWord; storage_len];
    let info = buffer.as_mut_ptr().cast::<FILE_RENAME_INFO>();
    unsafe {
        (*info).Anonymous = FILE_RENAME_INFO_0 {
            ReplaceIfExists: u8::from(replace),
        };
        (*info).RootDirectory = std::ptr::null_mut();
        (*info).FileNameLength = u32::try_from(name.len() * std::mem::size_of::<u16>())
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "rename target too long"))?;
        std::ptr::copy_nonoverlapping(
            name.as_ptr(),
            std::ptr::addr_of_mut!((*info).FileName).cast::<u16>(),
            name.len(),
        );
    }
    let renamed = unsafe {
        SetFileInformationByHandle(
            source.as_raw_handle(),
            FileRenameInfo,
            buffer.as_ptr().cast(),
            u32::try_from(total_size).map_err(|_| {
                io::Error::new(io::ErrorKind::InvalidInput, "rename buffer too large")
            })?,
        )
    };
    if renamed == 0 {
        let error = io::Error::last_os_error();
        if matches!(error.raw_os_error(), Some(80) | Some(183)) {
            return Err(io::Error::new(io::ErrorKind::AlreadyExists, error));
        }
        return Err(error);
    }
    Ok(())
}

#[cfg(windows)]
fn move_path_write_through(
    dir: &Dir,
    source: &OsStr,
    target: &OsStr,
    replace: bool,
) -> io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    use windows_sys::Win32::Storage::FileSystem::{
        MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
    };

    let parent = directory_path(dir)?;
    let source: Vec<u16> = parent
        .join(source)
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let target: Vec<u16> = parent
        .join(target)
        .as_os_str()
        .encode_wide()
        .chain(Some(0))
        .collect();
    let flags = MOVEFILE_WRITE_THROUGH
        | if replace {
            MOVEFILE_REPLACE_EXISTING
        } else {
            0
        };
    if unsafe { MoveFileExW(source.as_ptr(), target.as_ptr(), flags) } == 0 {
        let error = io::Error::last_os_error();
        if matches!(error.raw_os_error(), Some(80) | Some(183)) {
            return Err(io::Error::new(io::ErrorKind::AlreadyExists, error));
        }
        return Err(error);
    }
    Ok(())
}

#[cfg(windows)]
fn directory_path(dir: &Dir) -> io::Result<std::path::PathBuf> {
    use std::os::windows::ffi::OsStringExt;
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Storage::FileSystem::{
        GetFinalPathNameByHandleW, FILE_NAME_NORMALIZED,
    };

    let required = unsafe {
        GetFinalPathNameByHandleW(
            dir.as_raw_handle(),
            std::ptr::null_mut(),
            0,
            FILE_NAME_NORMALIZED,
        )
    };
    if required == 0 {
        return Err(io::Error::last_os_error());
    }
    let mut buffer = vec![0_u16; required as usize + 1];
    let written = unsafe {
        GetFinalPathNameByHandleW(
            dir.as_raw_handle(),
            buffer.as_mut_ptr(),
            buffer.len() as u32,
            FILE_NAME_NORMALIZED,
        )
    };
    if written == 0 || written as usize >= buffer.len() {
        return Err(io::Error::last_os_error());
    }
    Ok(std::path::PathBuf::from(OsString::from_wide(
        &buffer[..written as usize],
    )))
}

#[cfg(windows)]
fn hex_bytes(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len() * 2);
    const HEX: &[u8; 16] = b"0123456789abcdef";
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}

#[cfg(test)]
mod tests {
    #[test]
    fn temporary_publications_use_owner_only_unix_permissions() {
        assert_eq!(super::TEMPORARY_FILE_MODE, 0o600);
    }

    #[cfg(windows)]
    #[test]
    fn rename_info_storage_is_aligned_for_its_header() {
        use windows_sys::Win32::Storage::FileSystem::FILE_RENAME_INFO;

        assert!(
            std::mem::align_of::<super::RenameInfoStorageWord>()
                >= std::mem::align_of::<FILE_RENAME_INFO>()
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_staged_handles_are_write_through() {
        use std::os::windows::io::AsHandle;

        let root = std::env::temp_dir().join(format!(
            "anima-corefs-write-through-handle-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let dir = cap_std::fs::Dir::open_ambient_dir(&root, cap_std::ambient_authority()).unwrap();
        let (temporary, temporary_name) =
            super::create_temporary_in(&dir, std::ffi::OsStr::new("HEAD")).unwrap();

        let mode = winx::file::query_mode_information(temporary.as_handle()).unwrap();
        assert!(mode.contains(winx::file::FileModeInformation::FILE_WRITE_THROUGH));

        drop(temporary);
        dir.remove_file(temporary_name).unwrap();
        drop(dir);
        std::fs::remove_dir(root).unwrap();
    }
}
